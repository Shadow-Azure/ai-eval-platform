"""Delivery pipeline read model and controlled rollback (M5-18).

The ``deployment`` table is written by the GitHub Actions deploy workflow and
``backend/migrations/record_deployment.py`` (M2-02). This module turns those
rows into the console payload: the current/previous deployment, the five-stage
timeline, the deployment history and the safety checks for a manual rollback.

Rollback is deliberately split in two layers:

* the console API validates the target, the database compatibility window and
  the administrator confirmation, then writes an audit entry;
* the deploy workflow owns the actual host operation (``deploy/rollback.sh``)
  because only the ECS host can restore the previous Compose images.

The API never executes a database ``downgrade`` and never fabricates a stage
timeline; stages recorded by the deploy script are shown verbatim, historical
rows without stage detail are reported as unknown instead of invented.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Deployment, ServiceHeartbeat
from . import github_dispatch
from .object_store import ObjectStoreError, object_store_from_settings
from .operations import WORKER_HEARTBEAT_NAME

#: The five stages shown on the deployment page, matching the prototype. The
#: quality gate is read live from the commit's GitHub Actions runs; ``build``
#: happens before the host script starts; the other three are recorded by
#: ``deploy/deploy.sh``.
STAGE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("quality_gate", "测试与文档门禁"),
    ("build", "构建镜像并推送 ACR"),
    ("database_migration", "执行 PostgreSQL 迁移"),
    ("rolling_update", "ECS Compose 滚动更新"),
    ("health_check", "健康检查"),
)

#: Workflows whose result is the quality gate for a merge commit.
GATE_WORKFLOWS = frozenset({"Backend & Web CI", "Documentation Gates", "Flow Gates"})

_STAGE_STATUSES = {"success", "running", "failed", "unknown", "skipped"}

_ROLLBACK_REASONS: dict[str, str] = {
    "available": "可回滚到上一成功版本，仅替换应用镜像，数据库保持向前兼容。",
    "no_deployment": "还没有成功部署记录，无法回滚。",
    "no_previous": "当前只有一个成功版本，没有可回滚的上一版本。",
    "database_revision_unavailable": "无法读取当前数据库迁移版本，出于安全考虑禁止回滚。",
    "current_revision_mismatch": "当前部署记录与数据库迁移版本不一致，请先对齐版本。",
    "database_revision_mismatch": "上一版本的迁移版本与当前数据库不一致，禁止回滚应用镜像。",
    "github_not_configured": "平台未配置 GitHub 派发凭据，无法触发回滚工作流。",
}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _as_utc(value)
    return aware.isoformat(timespec="microseconds") if aware is not None else None


def _seconds(started: datetime | None, finished: datetime | None) -> int | None:
    begin = _as_utc(started)
    end = _as_utc(finished)
    if begin is None or end is None:
        return None
    return max(0, int((end - begin).total_seconds()))


def _short_commit(value: str) -> str:
    return value[:7] if value else "unknown"


def database_revision(session: Session) -> str | None:
    """Read the Alembic revision currently applied to the database."""

    try:
        revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:
        return None
    return str(revision) if revision is not None else None


def _stage_status(value: object) -> str:
    status = str(value or "unknown")
    return status if status in _STAGE_STATUSES else "unknown"


def _recorded_stages(row: Deployment) -> dict[str, dict[str, Any]]:
    detail = row.detail_json if isinstance(row.detail_json, dict) else {}
    recorded = detail.get("stages")
    if not isinstance(recorded, list):
        return {}
    stages: dict[str, dict[str, Any]] = {}
    for entry in recorded:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if isinstance(key, str) and key:
            stages[key] = entry
    return stages


def _inferred_status(row: Deployment, key: str) -> tuple[str, str]:
    """Status/detail for rows recorded before per-stage timings existed.

    A stored row means the GitHub build already succeeded. A succeeded
    deployment completed every host stage; a failed/deploying one does not tell
    us where it stopped, so the remaining stages stay ``unknown``.
    """

    if key == "quality_gate":
        return "unknown", "历史记录未保存 CI 门禁状态"
    if key == "build":
        return "success", "GitHub Actions 构建成功后触发部署"
    if row.status == "succeeded":
        return "success", "已完成（历史记录未保存分阶段耗时）"
    if row.status == "rolled_back":
        return "success", "回滚流程已完成"
    return "unknown", "历史记录未包含该阶段状态"


def stage_payloads(
    row: Deployment,
    *,
    ci_stage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    recorded = _recorded_stages(row)
    payload: list[dict[str, Any]] = []
    for key, label in STAGE_DEFINITIONS:
        if key == "quality_gate" and ci_stage is not None:
            payload.append(dict(ci_stage))
            continue
        entry = recorded.get(key)
        if entry is not None:
            status = _stage_status(entry.get("status"))
            started_at = entry.get("started_at")
            finished_at = entry.get("finished_at")
            duration = entry.get("duration_seconds")
            if not isinstance(duration, (int, float)):
                duration = _seconds(_parse_datetime(started_at), _parse_datetime(finished_at))
            payload.append(
                {
                    "key": key,
                    "label": label,
                    "status": status,
                    "detail": str(entry.get("detail") or ""),
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "duration_seconds": int(duration) if isinstance(duration, (int, float)) else None,
                }
            )
            continue
        status, detail = _inferred_status(row, key)
        payload.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "detail": detail,
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
            }
        )
    return payload


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def ci_gate_stage(settings: Settings, git_commit: str) -> dict[str, Any]:
    """Read the test/docs gate for one commit from GitHub Actions.

    The result is informational on the deployment page; an unavailable token,
    a network failure or a missing run is ``unknown`` — never ``success``.
    """

    stage: dict[str, Any] = {
        "key": "quality_gate",
        "label": "测试与文档门禁",
        "status": "unknown",
        "detail": "无法读取该提交的 CI 运行",
        "started_at": None,
        "finished_at": None,
        "duration_seconds": None,
    }
    if not settings.github_token:
        stage["detail"] = "未配置 GitHub 凭据，无法读取 CI 门禁"
        return stage
    try:
        runs = github_dispatch.list_workflow_runs_for_commit(git_commit, settings=settings)
    except github_dispatch.GitHubDispatchError:
        stage["detail"] = "GitHub Actions 查询失败，CI 门禁状态未知"
        return stage
    matching = [run for run in runs if str(run.get("name") or "") in GATE_WORKFLOWS]
    if not matching:
        stage["detail"] = "该提交没有可读取的 CI 运行"
        return stage

    statuses = {str(run.get("status") or "") for run in matching}
    conclusions = {str(run.get("conclusion") or "") for run in matching}
    if statuses & {"queued", "in_progress", "requested", "waiting", "pending"}:
        stage["status"] = "running"
    elif conclusions & {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}:
        stage["status"] = "failed"
    elif all(
        str(run.get("status")) == "completed"
        and str(run.get("conclusion")) in {"success", "neutral", "skipped"}
        for run in matching
    ):
        stage["status"] = "success"
    else:
        stage["detail"] = "CI 运行尚未形成最终结论"

    durations: list[int] = []
    for run in matching:
        started = _parse_datetime(run.get("run_started_at"))
        updated = _parse_datetime(run.get("updated_at"))
        seconds = _seconds(started, updated)
        if seconds is not None:
            durations.append(seconds)
    if durations:
        stage["duration_seconds"] = max(durations)
    names = " · ".join(sorted({str(run.get("name")) for run in matching}))
    if stage["status"] == "running":
        stage["detail"] = f"{names} · 运行中"
    elif stage["status"] == "failed":
        stage["detail"] = f"{names} · 存在失败检查"
    elif stage["status"] == "success":
        stage["detail"] = names
    return stage


def github_links(settings: Settings) -> dict[str, str]:
    base = f"https://github.com/{settings.github_repo}"
    return {
        "repository_url": base,
        "actions_url": f"{base}/actions/workflows/deploy.yml",
        "commit_base_url": f"{base}/commit",
    }


def deployment_item(
    row: Deployment,
    settings: Settings,
    commits: dict[str, str],
    *,
    ci_stage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail = row.detail_json if isinstance(row.detail_json, dict) else {}
    links = github_links(settings)
    run_url = detail.get("run_url") if isinstance(detail.get("run_url"), str) else None
    run_id = detail.get("run_id")
    if not run_url and run_id:
        run_url = f"{links['repository_url']}/actions/runs/{run_id}"
    rollback_commit = commits.get(row.rollback_of_id) if row.rollback_of_id else None
    return {
        "id": row.id,
        "environment": row.environment,
        "git_commit": row.git_commit,
        "short_commit": _short_commit(row.git_commit),
        "commit_url": f"{links['commit_base_url']}/{row.git_commit}",
        "image_digest": row.image_digest,
        "migration_revision": row.migration_revision,
        "status": row.status,
        "triggered_by": row.triggered_by,
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "duration_seconds": _seconds(row.started_at, row.finished_at),
        "rollback_of_id": row.rollback_of_id,
        "rollback_of_short_commit": (
            _short_commit(rollback_commit) if rollback_commit else None
        ),
        "is_rollback": row.rollback_of_id is not None,
        "run_url": run_url,
        "detail": {
            "api_image": str(detail.get("api_image") or ""),
            "web_image": str(detail.get("web_image") or ""),
            "git_commit": str(detail.get("git_commit") or ""),
            "run_id": str(run_id) if run_id else None,
            "run_url": run_url,
        },
        "stages": stage_payloads(row, ci_stage=ci_stage),
    }


def _latest_succeeded(session: Session, environment: str, limit: int) -> list[Deployment]:
    return list(
        session.scalars(
            select(Deployment)
            .where(
                Deployment.environment == environment,
                Deployment.status == "succeeded",
            )
            .order_by(Deployment.started_at.desc(), Deployment.id.desc())
            .limit(limit)
        ).all()
    )


def _component(key: str, name: str, status: str, summary: str) -> dict[str, Any]:
    return {"key": key, "name": name, "status": status, "summary": summary}


def _worker_component(
    session: Session, settings: Settings, now: datetime
) -> dict[str, Any]:
    beat = session.get(ServiceHeartbeat, WORKER_HEARTBEAT_NAME)
    if beat is None:
        return _component("worker", "调度 Worker", "unknown", "尚未收到 Worker 心跳")
    beat_at = _as_utc(beat.beat_at)
    if beat_at is None:
        return _component("worker", "调度 Worker", "unknown", "Worker 心跳缺少时间")
    age = max(0, int((now - beat_at).total_seconds()))
    threshold = max(30, int(settings.worker_poll_seconds * 3))
    if age > threshold:
        return _component("worker", "调度 Worker", "error", f"心跳已过期 {age} 秒")
    return _component("worker", "调度 Worker", "ok", f"最近心跳 {age} 秒前")


def component_health(session: Session, settings: Settings) -> dict[str, Any]:
    """Compact component status for the deployment page.

    Only status and a one-line summary leave this function; capacity, latency,
    bucket names and other administrator diagnostics stay on ``/operations``
    (see ADR 0002). The probe is intentionally best-effort so one unavailable
    dependency cannot break the whole deployment page.
    """

    now = datetime.now(UTC)
    components: list[dict[str, Any]] = [
        _component("api", "控制面 API", "ok", "请求处理正常"),
    ]

    database_ok = False
    try:
        session.execute(text("SELECT 1"))
        database_ok = True
        components.append(_component("database", "PostgreSQL", "ok", "连接正常"))
    except Exception:
        components.append(_component("database", "PostgreSQL", "error", "数据库查询失败"))

    if not settings.redis_url:
        components.append(_component("redis", "Redis", "disabled", "未配置 Redis"))
    else:
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            client.ping()
            components.append(_component("redis", "Redis", "ok", "连接正常"))
        except Exception:
            components.append(_component("redis", "Redis", "error", "连接失败"))

    store = object_store_from_settings(settings)
    if store is None:
        components.append(
            _component("object_storage", "对象存储", "disabled", "未配置对象存储")
        )
    else:
        try:
            store.head_bucket(timeout=2.0)
            components.append(_component("object_storage", "对象存储", "ok", "bucket 可访问"))
        except ObjectStoreError:
            components.append(
                _component("object_storage", "对象存储", "error", "bucket 探活失败")
            )
        except Exception:
            components.append(
                _component("object_storage", "对象存储", "error", "bucket 探活失败")
            )

    if database_ok:
        components.append(_worker_component(session, settings, now))
    else:
        components.append(
            _component("worker", "调度 Worker", "unknown", "数据库不可用，无法读取心跳")
        )

    healthy = sum(1 for component in components if component["status"] == "ok")
    return {
        "status": "ok" if healthy == len(components) else "degraded",
        "healthy": healthy,
        "total": len(components),
        "components": components,
    }


def rollback_state(
    *,
    current: Deployment | None,
    previous: Deployment | None,
    db_revision: str | None,
    settings: Settings,
) -> dict[str, Any]:
    reason = "available"
    if current is None:
        reason = "no_deployment"
    elif previous is None:
        reason = "no_previous"
    elif db_revision is None:
        reason = "database_revision_unavailable"
    elif current.migration_revision != db_revision:
        reason = "current_revision_mismatch"
    elif previous.migration_revision != db_revision:
        reason = "database_revision_mismatch"
    elif not settings.github_token:
        reason = "github_not_configured"
    return {
        "available": reason == "available",
        "reason": reason,
        "message": _ROLLBACK_REASONS.get(reason, "无法回滚"),
        "database_revision": db_revision,
    }


def deployments_payload(
    session: Session,
    settings: Settings,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    environment = settings.deploy_environment
    rows = list(
        session.scalars(
            select(Deployment)
            .where(Deployment.environment == environment)
            .order_by(Deployment.started_at.desc(), Deployment.id.desc())
            .limit(limit + 1)
        ).all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    latest_succeeded = _latest_succeeded(session, environment, 2)
    current = latest_succeeded[0] if latest_succeeded else None
    previous = latest_succeeded[1] if len(latest_succeeded) > 1 else None

    rollback_ids = {row.rollback_of_id for row in rows if row.rollback_of_id}
    commit_by_id: dict[str, str] = {}
    if rollback_ids:
        for referenced in session.scalars(
            select(Deployment).where(Deployment.id.in_(rollback_ids))
        ).all():
            commit_by_id[referenced.id] = referenced.git_commit

    db_revision = database_revision(session)
    items = [deployment_item(row, settings, commit_by_id) for row in rows]
    current_payload = (
        deployment_item(
            current,
            settings,
            commit_by_id,
            ci_stage=ci_gate_stage(settings, current.git_commit),
        )
        if current
        else None
    )
    previous_payload = (
        deployment_item(previous, settings, commit_by_id) if previous else None
    )
    rollback = rollback_state(
        current=current,
        previous=previous,
        db_revision=db_revision,
        settings=settings,
    )
    rollback["target"] = previous_payload

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": environment,
        "github": github_links(settings),
        "current": current_payload,
        "previous": previous_payload,
        "latest": items[0] if items else None,
        "database": {
            "revision": db_revision,
            "deployment_revision": current.migration_revision if current else None,
            "in_sync": (
                None
                if current is None or db_revision is None
                else current.migration_revision == db_revision
            ),
        },
        "health": component_health(session, settings),
        "rollback": rollback,
        "items": items,
        "has_more": has_more,
    }


def get_deployment(session: Session, deployment_id: str) -> Deployment | None:
    return session.get(Deployment, deployment_id)


def dispatch_rollback(
    *,
    settings: Settings,
    target: Deployment,
    current: Deployment,
    requested_by: str,
    reason: str | None,
) -> dict[str, Any]:
    """Ask the deploy workflow to restore ``target``'s images on the host."""

    return github_dispatch.post_repository_dispatch(
        event_type=settings.deploy_rollback_event_type,
        client_payload={
            "deployment_id": target.id,
            "git_commit": target.git_commit,
            "current_deployment_id": current.id,
            "current_git_commit": current.git_commit,
            "requested_by": requested_by,
            "reason": reason or "",
        },
        settings=settings,
    )

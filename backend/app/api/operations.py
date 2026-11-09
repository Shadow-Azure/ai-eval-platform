"""Administrator operations status (M3-06).

One read-only view for the console administrator: platform service liveness,
execution resources, and database facts. The endpoint is the only place that
combines these checks so the page does not fan out into many privileged
requests. It never returns connection strings, access keys, or any other
credential material.

Access is restricted to an administrator membership; a read-only member gets
403 and an anonymous request gets 401 through ``require_admin``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..models import (
    AuditLog,
    Credential,
    Dataset,
    DatasetCase,
    DatasetVersion,
    EvalRun,
    ModelConfig,
    Outbox,
    ResourcePool,
    Runner,
    ServiceHeartbeat,
    User,
)
from ..services.identity import AuthContext
from ..services.object_store import ObjectStoreError, object_store_from_settings
from ..services.operations import WORKER_HEARTBEAT_NAME
from .dependencies import require_admin

router = APIRouter(tags=["operations"])

# (model, Chinese label) for the database inventory. The deployment table has
# no ORM model and is counted separately with raw SQL.
_TABLE_INVENTORY: list[tuple[Any, str]] = [
    (EvalRun, "评测任务"),
    (Dataset, "数据集"),
    (DatasetVersion, "数据集版本"),
    (DatasetCase, "数据集用例"),
    (Runner, "执行 Runner"),
    (ResourcePool, "资源池"),
    (ModelConfig, "模型配置"),
    (User, "控制面账号"),
    (AuditLog, "审计记录"),
    (Outbox, "调度队列"),
    (ServiceHeartbeat, "服务心跳"),
]

_FIXED_STATUSES = {"ok", "disabled", "unknown"}


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _as_utc(value)
    return aware.isoformat() if aware is not None else None


def _service(
    key: str,
    name: str,
    status: str,
    summary: str,
    latency_ms: int | None = None,
) -> dict[str, object]:
    return {
        "key": key,
        "name": name,
        "status": status,
        "summary": summary,
        "latency_ms": latency_ms,
    }


def _count(session: Session, model: Any, *conditions: Any) -> int:
    statement = select(func.count()).select_from(model)
    for condition in conditions:
        statement = statement.where(condition)
    return int(session.scalar(statement) or 0)


def _database_size(session: Session) -> int | None:
    try:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            size = session.scalar(text("SELECT pg_database_size(current_database())"))
            return int(size) if size is not None else None
        if dialect == "sqlite":
            page_count = session.scalar(text("PRAGMA page_count"))
            page_size = session.scalar(text("PRAGMA page_size"))
            if page_count is None or page_size is None:
                return None
            return int(page_count) * int(page_size)
    except Exception:  # pragma: no cover - dialect-specific probing failure
        return None
    return None


def _database_revision(session: Session) -> str | None:
    try:
        revision = session.scalar(text("SELECT version_num FROM alembic_version"))
        return str(revision) if revision is not None else None
    except Exception:
        return None


def _table_rows(session: Session) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for model, label in _TABLE_INVENTORY:
        try:
            count: int | None = _count(session, model)
        except Exception:  # pragma: no cover - individual table probing failure
            count = None
        rows.append({"name": model.__tablename__, "label": label, "rows": count})
    try:
        deployment_rows = int(
            session.scalar(text("SELECT COUNT(*) FROM deployment")) or 0
        )
    except Exception:
        deployment_rows = None
    rows.append({"name": "deployment", "label": "部署记录", "rows": deployment_rows})
    return rows


def _percent(used: int | None, limit: int | None) -> float | None:
    if used is None or limit is None or limit <= 0:
        return None
    return round(used / limit * 100, 1)


def _database_capacity(
    session: Session,
    settings: Settings,
    size_bytes: int | None,
) -> dict[str, object]:
    max_connections: int | None = None
    connections: int | None = None
    try:
        if session.get_bind().dialect.name == "postgresql":
            raw = session.scalar(text("SHOW max_connections"))
            max_connections = int(raw) if raw is not None else None
            used = session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database()"
                )
            )
            connections = int(used) if used is not None else None
    except Exception:  # pragma: no cover - engine-specific capacity probe
        max_connections = None
        connections = None
    storage_limit = settings.database_storage_limit_bytes or None
    return {
        "max_connections": max_connections,
        "connections": connections,
        "connection_usage_percent": _percent(connections, max_connections),
        "storage_limit_bytes": storage_limit,
        "storage_usage_percent": _percent(size_bytes, storage_limit),
    }


def _database_status(
    session: Session,
    settings: Settings,
) -> tuple[dict[str, object], bool]:
    started = perf_counter()
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        return (
            {
                "status": "error",
                "dialect": None,
                "revision": None,
                "size_bytes": None,
                "capacity": {
                    "max_connections": None,
                    "connections": None,
                    "connection_usage_percent": None,
                    "storage_limit_bytes": settings.database_storage_limit_bytes or None,
                    "storage_usage_percent": None,
                },
                "latency_ms": _elapsed_ms(started),
                "tables": [],
            },
            False,
        )

    try:
        dialect = session.get_bind().dialect.name
    except Exception:  # pragma: no cover - defensive
        dialect = None
    size_bytes = _database_size(session)
    return (
        {
            "status": "ok",
            "dialect": dialect,
            "revision": _database_revision(session),
            "size_bytes": size_bytes,
            "capacity": _database_capacity(session, settings, size_bytes),
            "latency_ms": _elapsed_ms(started),
            "tables": _table_rows(session),
        },
        True,
    )


def _redis_service(settings: Settings) -> dict[str, object]:
    if not settings.redis_url:
        return _service("redis", "Redis", "disabled", "未配置 Redis；当前使用数据库队列")
    started = perf_counter()
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
    except Exception as exc:
        return _service(
            "redis",
            "Redis",
            "error",
            f"连接失败（{type(exc).__name__}）",
            _elapsed_ms(started),
        )
    return _service("redis", "Redis", "ok", "PING 正常", _elapsed_ms(started))


def _object_storage_service(settings: Settings) -> dict[str, object]:
    store = object_store_from_settings(settings)
    if store is None:
        return _service(
            "object_storage",
            "对象存储",
            "disabled",
            "未配置对象存储端点或访问凭据",
        )
    started = perf_counter()
    try:
        store.head_bucket(timeout=3.0)
    except ObjectStoreError as exc:
        return _service(
            "object_storage",
            "对象存储",
            "error",
            f"bucket {store.bucket} 探活失败：{exc}",
            _elapsed_ms(started),
        )
    except Exception as exc:  # pragma: no cover - unexpected transport failure
        return _service(
            "object_storage",
            "对象存储",
            "error",
            f"bucket {store.bucket} 探活失败（{type(exc).__name__}）",
            _elapsed_ms(started),
        )
    return _service(
        "object_storage",
        "对象存储",
        "ok",
        f"bucket {store.bucket} 可访问",
        _elapsed_ms(started),
    )


def _resource_snapshot(session: Session, tenant_id: str) -> dict[str, object]:
    try:
        runners = list(
            session.scalars(
                select(Runner)
                .where(Runner.tenant_id == tenant_id)
                .order_by(Runner.last_heartbeat_at.desc().nullslast(), Runner.name)
            ).all()
        )
        pools = list(
            session.scalars(
                select(ResourcePool)
                .where(ResourcePool.tenant_id == tenant_id)
                .order_by(ResourcePool.name)
            ).all()
        )
    except Exception:  # pragma: no cover - database already reported as down
        runners = []
        pools = []

    pool_names = {pool.id: pool.name for pool in pools}
    stats: dict[str, dict[str, int]] = {
        pool.id: {"total": 0, "online": 0} for pool in pools
    }
    for runner in runners:
        bucket = stats.setdefault(runner.resource_pool_id, {"total": 0, "online": 0})
        bucket["total"] += 1
        if runner.status in ("online", "busy"):
            bucket["online"] += 1

    def _safe_count(model: Any, *conditions: Any) -> int:
        try:
            return _count(session, model, *conditions)
        except Exception:  # pragma: no cover - database already reported as down
            return 0

    return {
        "pools": [
            {
                "id": pool.id,
                "name": pool.name,
                "provider": pool.provider_key,
                "status": pool.status,
                "runners_total": stats[pool.id]["total"],
                "runners_online": stats[pool.id]["online"],
            }
            for pool in pools
        ],
        "runners": [
            {
                "id": runner.id,
                "name": runner.name,
                "provider_runner_id": runner.provider_runner_id,
                "pool": pool_names.get(runner.resource_pool_id, "未知资源池"),
                "status": runner.status,
                "labels": list(runner.labels_json or [])
                if isinstance(runner.labels_json, list)
                else [],
                "last_heartbeat_at": _iso(runner.last_heartbeat_at),
            }
            for runner in runners
        ],
        "evaluations": {
            "total": _safe_count(EvalRun, EvalRun.tenant_id == tenant_id),
            "running": _safe_count(
                EvalRun, EvalRun.tenant_id == tenant_id, EvalRun.status == "running"
            ),
            "queued": _safe_count(
                EvalRun, EvalRun.tenant_id == tenant_id, EvalRun.status == "queued"
            ),
            "failed": _safe_count(
                EvalRun, EvalRun.tenant_id == tenant_id, EvalRun.status == "failed"
            ),
        },
        "datasets": {
            "total": _safe_count(Dataset, Dataset.tenant_id == tenant_id),
            "versions": _safe_count(DatasetVersion, DatasetVersion.tenant_id == tenant_id),
            "cases": _safe_count(DatasetCase, DatasetCase.tenant_id == tenant_id),
        },
        "model_configs": {
            "total": _safe_count(ModelConfig, ModelConfig.tenant_id == tenant_id),
            "credentials": _safe_count(Credential, Credential.tenant_id == tenant_id),
            "usable": _usable_model_configs(session, tenant_id),
        },
        "outbox": {
            "pending": _safe_count(Outbox, Outbox.tenant_id == tenant_id, Outbox.status == "pending"),
            "processing": _safe_count(
                Outbox, Outbox.tenant_id == tenant_id, Outbox.status == "processing"
            ),
            "failed": _safe_count(Outbox, Outbox.tenant_id == tenant_id, Outbox.status == "failed"),
        },
    }


def _usable_model_configs(session: Session, tenant_id: str) -> int:
    """Configs bound to a live credential; the gateway rejects the rest."""

    try:
        statement = (
            select(func.count())
            .select_from(ModelConfig)
            .join(
                Credential,
                and_(
                    Credential.tenant_id == ModelConfig.tenant_id,
                    Credential.id == ModelConfig.credential_id,
                ),
            )
            .where(
                ModelConfig.tenant_id == tenant_id,
                ModelConfig.credential_id.is_not(None),
                Credential.revoked_at.is_(None),
            )
        )
        return int(session.scalar(statement) or 0)
    except Exception:  # pragma: no cover - database already reported as down
        return 0


def _runner_service(resources: dict[str, object]) -> dict[str, object]:
    runners = resources["runners"]
    assert isinstance(runners, list)
    total = len(runners)
    online = sum(1 for runner in runners if runner["status"] in ("online", "busy"))
    if total == 0:
        return _service("runner", "Runner 平面", "unknown", "尚未注册 Runner")
    if online == 0:
        return _service(
            "runner",
            "Runner 平面",
            "degraded",
            f"0 / {total} 在线；检查 Runner 心跳",
        )
    return _service("runner", "Runner 平面", "ok", f"{online} / {total} 在线")


def _worker_service(
    session: Session,
    settings: Settings,
    now: datetime,
) -> dict[str, object]:
    beat = session.get(ServiceHeartbeat, WORKER_HEARTBEAT_NAME)
    if beat is None:
        return _service("worker", "调度 Worker", "unknown", "尚未收到 Worker 心跳")
    beat_at = _as_utc(beat.beat_at)
    if beat_at is None:  # pragma: no cover - defensive
        return _service("worker", "调度 Worker", "unknown", "Worker 心跳缺少时间")
    age = max(0, int((now - beat_at).total_seconds()))
    threshold = max(30, int(settings.worker_poll_seconds * 3))
    if age > threshold:
        return _service(
            "worker",
            "调度 Worker",
            "error",
            f"心跳已过期 {age} 秒（阈值 {threshold} 秒）",
        )
    return _service("worker", "调度 Worker", "ok", f"最近心跳 {age} 秒前")


def _model_gateway_service(resources: dict[str, object]) -> dict[str, object]:
    configs = resources["model_configs"]
    assert isinstance(configs, dict)
    total = int(configs["total"])
    usable = int(configs["usable"])
    if total == 0:
        return _service("model_gateway", "模型网关", "unknown", "尚未登记模型配置")
    if usable == 0:
        return _service(
            "model_gateway",
            "模型网关",
            "degraded",
            f"{total} 个模型配置，尚未绑定有效凭据",
        )
    return _service(
        "model_gateway",
        "模型网关",
        "ok",
        f"{total} 个模型配置 · {usable} 个可用",
    )


@router.get("/api/v1/operations/status")
def operations_status(
    context: Annotated[AuthContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Aggregated read-only infrastructure status for administrators."""

    started = perf_counter()
    now = datetime.now(UTC)
    tenant_id = context.tenant.id
    database, database_ok = _database_status(session, settings)
    resources = _resource_snapshot(session, tenant_id) if database_ok else {
        "pools": [],
        "runners": [],
        "evaluations": {"total": 0, "running": 0, "queued": 0, "failed": 0},
        "datasets": {"total": 0, "versions": 0, "cases": 0},
        "model_configs": {"total": 0, "credentials": 0, "usable": 0},
        "outbox": {"pending": 0, "processing": 0, "failed": 0},
    }

    services = [
        _service("api", "控制面 API", "ok", "请求处理正常", _elapsed_ms(started)),
        _service(
            "database",
            "PostgreSQL",
            str(database["status"]),
            "连接正常" if database_ok else "数据库查询失败",
            int(database["latency_ms"]),
        ),
        _redis_service(settings),
        _object_storage_service(settings),
        _runner_service(resources),
        _worker_service(session, settings, now) if database_ok else _service(
            "worker", "调度 Worker", "unknown", "数据库不可用，无法读取心跳"
        ),
        _model_gateway_service(resources)
        if database_ok
        else _service("model_gateway", "模型网关", "unknown", "数据库不可用"),
    ]

    overall = (
        "ok"
        if all(str(service["status"]) in _FIXED_STATUSES for service in services)
        else "degraded"
    )

    return {
        "generated_at": now.isoformat(),
        "environment": settings.environment,
        "status": overall,
        "services": services,
        "resources": resources,
        "database": database,
    }

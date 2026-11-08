"""Case attempt state machine behind the runner contract callbacks (M2-05).

Every callback resolves to ONE transactional update:

    校验 attempt（存在 + 短期 token 绑定在 API 层完成）→ 校验
    sequence/迁移合法性 → 更新 attempt/case/run → 写 eval_event。

Rules pinned by tests/backend/test_case_callbacks.py:

- Duplicate callbacks (same event_id/sequence) answer 200 deduped and never
  advance state or counters twice (at-least-once delivery, contract README).
- Out-of-order / late callbacks answer 409 stale_* and never regress state —
  including after cancellation or a terminal attempt (迟到回调保留审计不覆盖).
- Completion verifies score/trace objects in the object store; missing or
  size-mismatched objects → artifact_missing, never a full success.
- Retryable failures below max_attempts create exactly ONE new attempt and
  one retry dispatch; the failed attempt is preserved for diagnosis.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox
from ..runner_contracts import validate_payload
from . import evaluations
from .evaluations import refresh_run_progress
from .object_store import ObjectMissing, ObjectSizeMismatch, object_store_from_settings

ACTIVE_ATTEMPT_STATUSES = (
    "pending",
    "dispatching",
    "queued",
    "leased",
    "running",
    "scoring",
    "uploading",
)

COMPLETION_TO_ATTEMPT_STATUS = {
    "succeeded": "succeeded",
    "failed": "failed",
    "timeout": "timeout",
    "cancelled": "cancelled",
}


class CaseCallbackError(Exception):
    """Base for callback rejections mapped to HTTP errors by the API layer."""

    status_code = 409
    code = "case_callback_invalid"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None) -> None:
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(message)


class CaseNotFound(CaseCallbackError):
    status_code = 404
    code = "case_run_not_found"


class StaleAttempt(CaseCallbackError):
    code = "stale_attempt"


class StaleSequence(CaseCallbackError):
    code = "stale_sequence"


class ArtifactMissing(CaseCallbackError):
    code = "artifact_missing"


class CaseContext:
    def __init__(self, session: Session, case_run: EvalCaseRun, run: EvalRun, attempt: EvalCaseAttempt) -> None:
        self.session = session
        self.case_run = case_run
        self.run = run
        self.attempt = attempt


def resolve_context(session: Session, *, case_run_id: str, attempt_no: int) -> CaseContext:
    """Load case + run + the exact attempt; unknown case/attempt is stale."""

    case_run = session.get(EvalCaseRun, case_run_id)
    if case_run is None:
        raise CaseNotFound(f"case_run 不存在：{case_run_id}")
    run = session.get(EvalRun, case_run.eval_run_id)
    if run is None:
        raise CaseNotFound(f"评测不存在：{case_run.eval_run_id}")
    attempt = session.scalar(
        select(EvalCaseAttempt).where(
            EvalCaseAttempt.eval_case_run_id == case_run_id,
            EvalCaseAttempt.attempt_no == attempt_no,
        )
    )
    if attempt is None:
        raise StaleAttempt(
            f"attempt {attempt_no} 不存在（最新 attempt_no={case_run.latest_attempt_no}）"
        )
    return CaseContext(session, case_run, run, attempt)


def _event_exists(ctx: CaseContext, *, sequence: int, event_id: str | None = None) -> bool:
    """A stored event means the callback was already applied. For terminal
    attempts the dedupe must match the event_id too: a DIFFERENT late event on
    the same sequence is not a retransmission and must stay stale (审计保留，
    状态不覆盖)."""

    statement = select(EvalEvent).where(
        EvalEvent.eval_case_run_id == ctx.case_run.id,
        EvalEvent.attempt_no == ctx.attempt.attempt_no,
        EvalEvent.sequence == sequence,
    )
    stored = ctx.session.scalars(statement).all()
    if not stored:
        return False
    if event_id is None:
        return True
    return any(event.payload_json.get("event_id") == event_id for event in stored)


def _add_event(ctx: CaseContext, *, event_type: str, sequence: int, payload: dict) -> None:
    ctx.session.add(
        EvalEvent(
            tenant_id=ctx.run.tenant_id,
            eval_run_id=ctx.run.id,
            eval_case_run_id=ctx.case_run.id,
            attempt_no=ctx.attempt.attempt_no,
            sequence=sequence,
            event_type=event_type,
            payload_json=payload,
        )
    )


def _resolve_runner_pk(session: Session, provider_runner_id: str | None) -> str | None:
    """Map the payload's provider runner id to the runner table PK (the
    attempt.runner_id column carries a real FK to runner.id)."""

    if not provider_runner_id:
        return None
    from ..models import Runner

    runner = session.scalar(
        select(Runner.id).where(Runner.provider_runner_id == provider_runner_id).limit(1)
    )
    return runner


def _promote_running(ctx: CaseContext, *, now: datetime, runner_id: str | None = None) -> bool:
    """Active → running transition; returns False when already running."""

    if ctx.attempt.status == "running":
        return False
    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝推进")
    ctx.attempt.status = "running"
    if ctx.attempt.started_at is None:
        ctx.attempt.started_at = now
    if runner_id:
        ctx.attempt.runner_id = runner_id
    if ctx.case_run.execution_status in ("queued", "cancelled"):
        ctx.case_run.execution_status = "running"
    if ctx.run.status == "queued":
        ctx.run.status = "running"
        if ctx.run.started_at is None:
            ctx.run.started_at = now
    return True


# ---------------------------------------------------------------------------
# started
# ---------------------------------------------------------------------------


def handle_started(session: Session, *, payload: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])
    if ctx.attempt.status == "running" and _event_exists(ctx, sequence=0, event_id=payload["event_id"]):
        return {"ok": True, "deduped": True, "status": "running"}
    if _event_exists(ctx, sequence=0, event_id=payload["event_id"]):
        return {"ok": True, "deduped": True, "status": ctx.attempt.status}

    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        # Late started after terminal (completed/cancelled): keep audit, no override.
        _add_event(ctx, event_type="case.started_late", sequence=0, payload={"event_id": payload["event_id"]})
        session.commit()
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝 started")

    _promote_running(
        ctx, now=now, runner_id=_resolve_runner_pk(ctx.session, payload.get("runner_id"))
    )
    ctx.attempt.heartbeat_at = now
    ctx.attempt.phase = "preparing"
    _add_event(
        ctx,
        event_type="case.started",
        sequence=0,
        payload={"event_id": payload["event_id"], "runner_id": payload.get("runner_id")},
    )
    refresh_run_progress(session, ctx.run, now=now)
    session.commit()
    return {"ok": True, "deduped": False, "status": ctx.attempt.status}


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------


def handle_heartbeat(session: Session, *, payload: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])
    sequence = payload["sequence"]

    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝心跳")
    if sequence < ctx.attempt.sequence:
        raise StaleSequence(
            f"心跳 sequence {sequence} 落后于当前 {ctx.attempt.sequence}"
        )
    if sequence == ctx.attempt.sequence:
        return {"ok": True, "deduped": True, "status": ctx.attempt.status}
    if _event_exists(ctx, sequence=sequence, event_id=payload["event_id"]):
        return {"ok": True, "deduped": True, "status": ctx.attempt.status}

    _promote_running(ctx, now=now)
    ctx.attempt.sequence = sequence
    ctx.attempt.phase = payload.get("phase")
    ctx.attempt.heartbeat_at = now
    _add_event(
        ctx,
        event_type="case.heartbeat",
        sequence=sequence,
        payload={
            "event_id": payload["event_id"],
            "phase": payload.get("phase"),
            "progress": payload.get("progress"),
        },
    )
    refresh_run_progress(session, ctx.run, now=now)
    session.commit()
    return {"ok": True, "deduped": False, "status": ctx.attempt.status}


# ---------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------


def _verify_objects(ctx: CaseContext, objects: list[dict], *, now: datetime, sequence: int = 0) -> None:
    settings = get_settings()
    store = object_store_from_settings(settings)
    if store is None:
        # No object store configured (pure-dev): accept manifests as-is.
        return
    try:
        for item in objects:
            try:
                store.verify_object(item["object_key"], expected_size=item.get("size"))
            except ObjectMissing as exc:
                raise ArtifactMissing(f"对象不存在：{item['object_key']}") from exc
            except ObjectSizeMismatch as exc:
                raise ArtifactMissing(str(exc)) from exc
    except ArtifactMissing:
        # 验收 4：对象缺失是可诊断失败态——attempt 落 artifact_missing、case 落
        # failed，绝不把用例标成完整成功。
        ctx.attempt.status = "artifact_missing"
        ctx.attempt.ended_at = now
        ctx.attempt.error_code = "artifact_missing"
        if ctx.case_run.execution_status not in evaluations.TERMINAL_CASE_STATUSES:
            ctx.case_run.execution_status = "failed"
        ctx.case_run.error_code = "artifact_missing"
        _add_event(
            ctx,
            event_type="case.artifact_missing",
            sequence=sequence,
            payload={"objects": [item["object_key"] for item in objects]},
        )
        refresh_run_progress(ctx.session, ctx.run, now=now)
        ctx.session.commit()
        raise


def handle_uploads(session: Session, *, payload: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])
    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝上传清单")

    _verify_objects(ctx, payload["objects"], now=now)
    _promote_running(ctx, now=now)
    merged = dict(ctx.attempt.artifact_manifest_json or {})
    merged.setdefault("objects", [])
    known = {obj.get("object_key") for obj in merged["objects"]}
    for item in payload["objects"]:
        if item["object_key"] not in known:
            merged["objects"].append(item)
    ctx.attempt.artifact_manifest_json = merged
    session.commit()
    return {"ok": True, "deduped": False, "status": ctx.attempt.status}


# ---------------------------------------------------------------------------
# completed
# ---------------------------------------------------------------------------


def handle_completed(session: Session, *, payload: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])

    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        if _event_exists(ctx, sequence=payload["sequence"], event_id=payload["event_id"]):
            return {"ok": True, "deduped": True, "status": ctx.attempt.status}
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝完成回调")

    _verify_objects(ctx, [payload["score_manifest"]], now=now, sequence=payload["sequence"])
    if "trace_manifest" in payload:
        _verify_objects(
            ctx, [payload["trace_manifest"]], now=now, sequence=payload["sequence"]
        )

    attempt_status = COMPLETION_TO_ATTEMPT_STATUS[payload["execution_status"]]
    ctx.attempt.status = attempt_status
    ctx.attempt.ended_at = now
    manifests = {"score_manifest": payload["score_manifest"]}
    if "trace_manifest" in payload:
        manifests["trace_manifest"] = payload["trace_manifest"]
    merged = dict(ctx.attempt.artifact_manifest_json or {})
    merged.update(manifests)
    ctx.attempt.artifact_manifest_json = merged
    ctx.case_run.execution_status = payload["execution_status"]
    ctx.case_run.result_status = payload["result_status"]
    ctx.case_run.duration_ms = payload["summary"].get("duration_ms")
    if attempt_status != "succeeded":
        ctx.case_run.error_code = f"runner_{payload['execution_status']}"
    trace_status = "ready" if "trace_manifest" in payload else "unlinked"
    ctx.case_run.trace_status = trace_status
    _add_event(
        ctx,
        event_type="case.completed",
        sequence=payload["sequence"],
        payload={
            "event_id": payload["event_id"],
            "execution_status": payload["execution_status"],
            "result_status": payload["result_status"],
            "score_manifest": payload["score_manifest"],
        },
    )
    refresh_run_progress(session, ctx.run, now=now)
    session.commit()
    return {"ok": True, "deduped": False, "status": ctx.attempt.status}


# ---------------------------------------------------------------------------
# failed
# ---------------------------------------------------------------------------


def build_retry_dispatch(
    session: Session, ctx: CaseContext, *, new_attempt_no: int, settings: Settings, now: datetime
) -> Outbox:
    """One single-case shard dispatch for the retry attempt.

    Attempt rows are ONLY created here and in evaluation creation — the
    dispatch loop's crash recovery cannot duplicate them.
    """

    import hashlib
    import json
    from datetime import timedelta
    from urllib.parse import urlencode

    run = ctx.run
    retry_shard_id = f"{ctx.attempt.shard_id}-o{ctx.case_run.ordinal}a{new_attempt_no}"[:64]
    dispatch_key = f"{run.id}:{retry_shard_id}:{new_attempt_no}"
    deadline = now + timedelta(minutes=settings.eval_shard_deadline_minutes)
    expires_at = now + timedelta(seconds=settings.eval_shard_ttl_seconds)
    manifest = {
        "schema_version": "1.0",
        "eval_run_id": run.id,
        "shard_id": retry_shard_id,
        "attempt": new_attempt_no,
        "dispatch_key": dispatch_key,
        "callback_base_url": settings.runner_callback_base_url,
        "deadline": deadline.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "case_timeout_seconds": run.case_timeout_seconds,
        "max_attempts": run.max_attempts,
        "cases": [
            {
                "case_run_id": ctx.case_run.id,
                "case_key": ctx.case_run.case_key,
                "dataset_case_id": ctx.case_run.dataset_case_id,
            }
        ],
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    manifest_digest = "sha256:" + hashlib.sha256(manifest_text.encode()).hexdigest()
    query = urlencode(
        {"eval_run_id": run.id, "attempt": new_attempt_no, "dispatch_key": dispatch_key}
    )
    manifest_url = (
        f"{settings.runner_callback_base_url.rstrip('/')}"
        f"{evaluations.MANIFEST_PATH_TEMPLATE.format(shard_id=retry_shard_id)}?{query}"
    )
    client_payload = {
        "schema_version": "1.0",
        "eval_run_id": run.id,
        "shard_id": retry_shard_id,
        "attempt": new_attempt_no,
        "dispatch_key": dispatch_key,
        "manifest_url": manifest_url,
        "manifest_digest": manifest_digest,
        "expires_at": expires_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }
    validate_payload("dispatch", client_payload)

    retry_attempt = EvalCaseAttempt(
        tenant_id=run.tenant_id,
        eval_case_run_id=ctx.case_run.id,
        attempt_no=new_attempt_no,
        status="pending",
        dispatch_status="pending",
        shard_id=retry_shard_id,
        dispatch_key=f"{dispatch_key}:{ctx.case_run.id}",
        resource_pool_id=run.resource_pool_id,
        retry_reason="runner_retryable_failure",
    )
    session.add(retry_attempt)
    ctx.case_run.latest_attempt_no = new_attempt_no
    return Outbox(
        tenant_id=run.tenant_id,
        topic=evaluations.SHARD_DISPATCH_TOPIC,
        aggregate_type="eval_run",
        aggregate_id=run.id,
        dispatch_key=dispatch_key,
        operation="repository_dispatch",
        payload_json={
            "client_payload": client_payload,
            "manifest_text": manifest_text,
            "manifest_digest": manifest_digest,
            "attempt": new_attempt_no,
            "shard_id": retry_shard_id,
        },
        status="pending",
    )


def handle_failed(
    session: Session,
    *,
    payload: dict,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict:
    settings = settings or Settings()
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])

    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        if _event_exists(ctx, sequence=payload["sequence"], event_id=payload["event_id"]):
            return {"ok": True, "deduped": True, "status": ctx.attempt.status}
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝失败回调")

    ctx.attempt.status = "failed"
    ctx.attempt.ended_at = now
    ctx.attempt.error_code = payload["error_code"]
    ctx.attempt.error_summary = payload["error_summary"]
    ctx.attempt.retry_reason = "retryable" if payload["retryable"] else "permanent"
    _add_event(
        ctx,
        event_type="case.failed",
        sequence=payload["sequence"],
        payload={
            "event_id": payload["event_id"],
            "error_code": payload["error_code"],
            "retryable": payload["retryable"],
            "error_summary": payload["error_summary"][:200],
        },
    )

    new_attempt_no = ctx.attempt.attempt_no + 1
    can_retry = (
        payload["retryable"]
        and new_attempt_no <= ctx.run.max_attempts
        and ctx.run.status not in evaluations.TERMINAL_RUN_STATUSES
    )
    if can_retry:
        outbox_row = build_retry_dispatch(
            session, ctx, new_attempt_no=new_attempt_no, settings=settings, now=now
        )
        session.add(outbox_row)
        ctx.case_run.execution_status = "queued"
        ctx.case_run.error_code = payload["error_code"]
        _add_event(
            ctx,
            event_type="case.retry_queued",
            sequence=payload["sequence"] + 1,
            payload={
                "new_attempt_no": new_attempt_no,
                "shard_id": outbox_row.payload_json["shard_id"],
                "dispatch_key": outbox_row.dispatch_key,
            },
        )
    else:
        if ctx.case_run.execution_status not in evaluations.TERMINAL_CASE_STATUSES:
            ctx.case_run.execution_status = "failed"
        ctx.case_run.error_code = payload["error_code"]
        ctx.case_run.error_summary = payload["error_summary"][:1000]

    refresh_run_progress(session, ctx.run, now=now)
    session.commit()
    return {
        "ok": True,
        "deduped": False,
        "status": ctx.attempt.status,
        "retry_queued": can_retry,
    }

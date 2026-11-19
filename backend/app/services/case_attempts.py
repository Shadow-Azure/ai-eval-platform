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
  size-mismatched objects → artifact_missing, never a full success. The
  post-verification body lives in ``finalize_completion`` so the reconciler's
  lost-callback backfill reuses it instead of duplicating the state machine.
- Cancellation (M4-02 A2): a ``cancelling``/``cancelled`` run refuses new
  ``started`` callbacks with 409 ``run_cancelled`` (audited) while late
  uploads/completions from already-started attempts are still accepted and
  settle the run as cancelled.
- Retryable failures below max_attempts create exactly ONE new attempt and
  one retry dispatch; the failed attempt is preserved for diagnosis.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..agent_timeout import DEFAULT_AGENT_TIMEOUT_SECONDS
from ..config import Settings, get_settings
from ..models import (
    DatasetVersionMetric,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    MetricDefinition,
    Outbox,
    ResourcePool,
)
from ..runner_contracts import validate_payload
from ..scoring.service import ingest_score_report, recompute_metrics
from . import capacity, evaluations, lifecycle, queue_lease, spot_fallback
from .evaluations import refresh_run_progress
from .object_store import ObjectMissing, ObjectSizeMismatch, object_store_from_settings
from .trace_manifest import (
    TRACE_ARTIFACT_KINDS,
    TraceManifestError,
    merge_manifest_objects,
    normalize_trace_manifest,
    normalize_upload_objects,
    trace_objects,
    trace_status_for,
)

ACTIVE_ATTEMPT_STATUSES = evaluations.ACTIVE_ATTEMPT_STATUSES

logger = logging.getLogger(__name__)

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


class ManifestInvalid(CaseCallbackError):
    status_code = 422
    code = "manifest_invalid"


class ManifestConflict(CaseCallbackError):
    status_code = 409
    code = "manifest_conflict"


def _manifest_error(exc: TraceManifestError) -> CaseCallbackError:
    return ManifestConflict(str(exc)) if exc.code == "manifest_conflict" else ManifestInvalid(str(exc))


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


def next_free_sequence(session: Session, case_run: EvalCaseRun, attempt: EvalCaseAttempt) -> int:
    """First sequence number free for this (case, attempt) row.

    Reused by the reconciler's backfilled completion: its event must not collide
    with the started (0), the last heartbeat sequence or a late callback.
    """

    highest = int(
        session.scalar(
            select(func.max(EvalEvent.sequence)).where(
                EvalEvent.eval_case_run_id == case_run.id,
                EvalEvent.attempt_no == attempt.attempt_no,
            )
        )
        or 0
    )
    return max(highest, int(attempt.sequence or 0)) + 1


def attempt_event_exists(
    session: Session, *, case_run_id: str, attempt_no: int, event_type: str
) -> bool:
    """Whether this attempt already has an event of ``event_type`` (dedupe)."""

    return (
        session.scalar(
            select(EvalEvent.id)
            .where(
                EvalEvent.eval_case_run_id == case_run_id,
                EvalEvent.attempt_no == attempt_no,
                EvalEvent.event_type == event_type,
            )
            .limit(1)
        )
        is not None
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
    """Active → running transition; returns False when already past running.

    ``uploading``/``scoring`` are later phases: a heartbeat or a late upload
    manifest must not move the attempt backwards (the phase clock would lie).
    """

    if ctx.attempt.status in ("running", "scoring", "uploading"):
        return False
    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝推进")
    lifecycle.mark_status(ctx.attempt, "running", now)
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
    # M8-45 (#307): a start is real shard progress, so every still-waiting
    # sibling gets a fresh queue lease before this transaction commits.
    queue_lease.renew_shard_leases(
        ctx.session,
        attempt=ctx.attempt,
        run=ctx.run,
        settings=get_settings(),
        now=now,
    )
    return True


# ---------------------------------------------------------------------------
# started
# ---------------------------------------------------------------------------


def _enforce_capacity_gate(ctx: CaseContext, *, now: datetime) -> None:
    """Hard per-evaluation concurrency cap before an attempt starts (M4-01).

    ``started`` is the one-and-only transition per case and happens before the
    agent starts, so rejecting here never leaves a half-run case. The attempt
    stays ``queued``: no event, no attempt mutation, and the runner retries the
    same event id until capacity frees or its wait budget expires.

    When capacity metadata is unavailable (cap == 0) an already-dispatched job
    is allowed through; the dispatcher gate still blocks new shards, and
    rejecting here could deadlock a live job while the runner table is stale.
    """

    run = ctx.run
    if not run.resource_pool_id or run.status not in ("queued", "running"):
        return
    pool = ctx.session.get(ResourcePool, run.resource_pool_id)
    pool_max = capacity.pool_max_capacity(pool) if pool is not None else None
    if pool is not None and pool_max is not None:
        # M8-44 (#306): lock the pool BEFORE flushing the eval_run write. The
        # pool reconciler holds the pool row and then updates eval_run while
        # reclaiming an attempt; taking eval_run first here would invert the
        # lock order and deadlock (ABBA) on PostgreSQL.
        capacity.lock_pool(ctx.session, pool.id)
    cap = capacity.effective_concurrency(ctx.session, run, settings=get_settings(), now=now)
    run.effective_concurrency = cap
    # The count below is check-then-act, so serialize concurrent started
    # callbacks by flushing this write: Postgres takes the eval_run row lock,
    # SQLite takes the database write lock (SELECT ... FOR UPDATE is a no-op on
    # SQLite, so a read lock would not protect the cap).
    ctx.session.flush()
    if pool is not None and pool_max is not None:
        # The per-run cap above is not enough when two evaluations share one
        # pool. Refuse the start once the whole pool has max_capacity Agent
        # slots in use, regardless of which run owns them.
        started_in_pool = capacity.pool_started_attempts(ctx.session, pool)
        if started_in_pool >= pool_max:
            run.queue_reason = capacity.QUEUE_REASON_CONCURRENCY_CAP
            ctx.session.commit()
            raise CaseCallbackError(
                "资源池并发已达上限，请稍后重试",
                code="capacity_waiting",
                status_code=429,
            )
    if cap <= 0:
        run.queue_reason = capacity.QUEUE_REASON_CAPACITY_WAITING
        return
    running = int(
        ctx.session.scalar(
            select(func.count())
            .select_from(EvalCaseRun)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseRun.execution_status == "running",
                EvalCaseRun.id != ctx.case_run.id,
            )
        )
        or 0
    )
    if running >= cap:
        run.queue_reason = capacity.QUEUE_REASON_CONCURRENCY_CAP
        ctx.session.commit()
        raise CaseCallbackError(
            "评测并发已达上限，请稍后重试",
            code="capacity_waiting",
            status_code=429,
        )
    run.queue_reason = None


def _reject_started_after_cancel(ctx: CaseContext, *, payload: dict) -> None:
    """A cancelled run must never let an agent start (M4-02 A2).

    The attempt is left untouched (it may already be ``cancelled`` or still be
    settling); the rejection is recorded as ``case.started_rejected`` so the
    runner's retry loop is auditable. Deduped on the callback event id so a
    retried started does not spam the event stream.
    """

    event_id = payload.get("event_id")
    stored = ctx.session.scalars(
        select(EvalEvent).where(
            EvalEvent.eval_case_run_id == ctx.case_run.id,
            EvalEvent.attempt_no == ctx.attempt.attempt_no,
            EvalEvent.event_type == "case.started_rejected",
        )
    ).all()
    if not any((event.payload_json or {}).get("event_id") == event_id for event in stored):
        _add_event(
            ctx,
            event_type="case.started_rejected",
            sequence=next_free_sequence(ctx.session, ctx.case_run, ctx.attempt),
            payload={
                "event_id": event_id,
                "reason": "run_cancelled",
                "run_status": ctx.run.status,
                "runner_id": payload.get("runner_id"),
            },
        )
        ctx.session.commit()
    raise CaseCallbackError("评测已取消，拒绝启动", code="run_cancelled", status_code=409)


def _reject_started_for_pause(ctx: CaseContext, *, payload: dict) -> None:
    """A budget-paused run must not let a queued Job start an agent (M4-05).

    The attempt is left to the pause reconciler (which settles not-started
    attempts); the rejection is recorded as ``case.started_rejected`` so the
    runner's retry loop stays auditable.
    """

    event_id = payload.get("event_id")
    stored = ctx.session.scalars(
        select(EvalEvent).where(
            EvalEvent.eval_case_run_id == ctx.case_run.id,
            EvalEvent.attempt_no == ctx.attempt.attempt_no,
            EvalEvent.event_type == "case.started_rejected",
        )
    ).all()
    if not any((event.payload_json or {}).get("event_id") == event_id for event in stored):
        _add_event(
            ctx,
            event_type="case.started_rejected",
            sequence=next_free_sequence(ctx.session, ctx.case_run, ctx.attempt),
            payload={
                "event_id": event_id,
                "reason": evaluations.BUDGET_PAUSED,
                "run_status": ctx.run.status,
                "runner_id": payload.get("runner_id"),
            },
        )
        ctx.session.commit()
    raise CaseCallbackError(
        "模型预算已暂停，拒绝启动新 Agent",
        code=evaluations.BUDGET_PAUSED,
        status_code=409,
    )


def _recover_started_sequence_conflict(
    session: Session, *, payload: dict
) -> dict:
    """Resolve a sequence-0 collision without turning the callback into a 500.

    The manual-retry path used to write ``case.retry_requested`` on the NEW
    attempt with the model default sequence 0; the Runner's ``case.started``
    then hit ``uq_eval_event_case_attempt_sequence``. New rows use the
    platform reserved sequence, but attempts created before the deploy still
    exist. Relocate those platform events into the reserved POSITIVE range and
    retry the started callback once; a genuine duplicate started is deduped,
    anything else is a diagnosable 409 instead of a 500.

    The reserved range must stay positive: the console's event reader rejects
    negative sequences, and moving to 1 would collide with the Runner's first
    heartbeat.
    """

    ctx = resolve_context(
        session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"]
    )
    if _event_exists(ctx, sequence=0, event_id=payload["event_id"]):
        return {"ok": True, "deduped": True, "status": ctx.attempt.status}
    stored = list(
        session.scalars(
            select(EvalEvent).where(
                EvalEvent.eval_case_run_id == ctx.case_run.id,
                EvalEvent.attempt_no == ctx.attempt.attempt_no,
                EvalEvent.sequence == 0,
            )
        )
    )
    legacy_platform_events = [
        event
        for event in stored
        if event.event_type in ("case.retry_requested", "case.retry_queued")
    ]
    if not legacy_platform_events:
        raise CaseCallbackError(
            "started 事件序号冲突，请稍后重试",
            code="sequence_conflict",
            status_code=409,
        )
    used = set(
        session.scalars(
            select(EvalEvent.sequence).where(
                EvalEvent.eval_case_run_id == ctx.case_run.id,
                EvalEvent.attempt_no == ctx.attempt.attempt_no,
            )
        )
    )
    next_platform = lifecycle.PLATFORM_EVENT_SEQUENCE
    for event in legacy_platform_events:
        while next_platform in used:
            next_platform += 1
        event.sequence = next_platform
        used.add(next_platform)
        next_platform += 1
    session.commit()
    return {"ok": True, "recovered": True}


def handle_started(
    session: Session,
    *,
    payload: dict,
    now: datetime | None = None,
    _recovery_depth: int = 0,
) -> dict:
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])
    if ctx.run.status in ("cancelling", "cancelled"):
        # Cancellation revocation point: the token may still be valid for the
        # TTL, so the run status is checked before any state mutation.
        _reject_started_after_cancel(ctx, payload=payload)
    if ctx.run.status in evaluations.PAUSED_RUN_STATUSES:
        # Budget revocation point: no new agent may start while the platform is
        # pausing or waiting for the operator to raise the budget.
        _reject_started_for_pause(ctx, payload=payload)
    if ctx.attempt.status == "running" and _event_exists(ctx, sequence=0, event_id=payload["event_id"]):
        # Same event id from a different Runner means a duplicate Job on another
        # machine. Only the runner that owns the attempt may continue; the other
        # must stop before starting an agent (cross-machine duplicate guard).
        incoming = _resolve_runner_pk(session, payload.get("runner_id"))
        if incoming and ctx.attempt.runner_id and incoming != ctx.attempt.runner_id:
            raise StaleAttempt("该用例已由其他 Runner 执行，拒绝重复启动")
        return {"ok": True, "deduped": True, "status": "running"}
    if _event_exists(ctx, sequence=0, event_id=payload["event_id"]):
        return {"ok": True, "deduped": True, "status": ctx.attempt.status}

    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        # Late started after terminal (completed/cancelled): keep audit, no override.
        _add_event(ctx, event_type="case.started_late", sequence=0, payload={"event_id": payload["event_id"]})
        session.commit()
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝 started")

    _enforce_capacity_gate(ctx, now=now)

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
    try:
        # refresh_run_progress flushes; the unique-key violation surfaces here,
        # not only at commit.
        refresh_run_progress(session, ctx.run, now=now)
        session.commit()
    except IntegrityError:
        # At-least-once callbacks and pre-deploy manual retries can race on the
        # natural key (case, attempt, sequence=0). Roll back this attempt and
        # recover idempotently instead of surfacing a 500 to the Runner.
        session.rollback()
        if _recovery_depth >= 1:
            raise CaseCallbackError(
                "started 事件序号冲突，请稍后重试",
                code="sequence_conflict",
                status_code=409,
            ) from None
        _recover_started_sequence_conflict(session, payload=payload)
        return handle_started(
            session, payload=payload, now=now, _recovery_depth=_recovery_depth + 1
        )
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


def _expected_trace_object_key(ctx: CaseContext, item: dict) -> str:
    return (
        f"evaluations/{ctx.run.id}/{ctx.attempt.shard_id}"
        f"/attempt{ctx.attempt.attempt_no}/{ctx.case_run.id}"
        f"/{item['artifact_kind']}/{item['sha256']}"
    )


def _verify_trace_object_ownership(ctx: CaseContext, objects: list[dict]) -> None:
    """Reject trace objects whose platform key belongs to another attempt."""

    for item in objects:
        if item.get("artifact_kind") not in TRACE_ARTIFACT_KINDS:
            continue
        expected = _expected_trace_object_key(ctx, item)
        if item.get("object_key") != expected:
            raise ManifestInvalid(
                f"轨迹对象键不属于当前 attempt：{item.get('object_key')}"
            )


def _verify_objects(ctx: CaseContext, objects: list[dict], *, now: datetime, sequence: int = 0) -> None:
    """Verify object existence/size, then trace-object attempt ownership."""

    settings = get_settings()
    store = object_store_from_settings(settings)
    if store is None:
        # No object store configured (pure-dev): still reject trace keys that
        # claim another attempt's platform prefix.
        _verify_trace_object_ownership(ctx, objects)
        return

    def verify_one(item: dict) -> None:
        try:
            store.verify_object(item["object_key"], expected_size=item.get("size"))
        except ObjectMissing as exc:
            raise ArtifactMissing(f"对象不存在：{item['object_key']}") from exc
        except ObjectSizeMismatch as exc:
            raise ArtifactMissing(str(exc)) from exc

    try:
        executor = ThreadPoolExecutor(max_workers=min(8, max(1, len(objects))))
        try:
            futures = [executor.submit(verify_one, item) for item in objects]
            for future in as_completed(futures):
                future.result()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
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
    _verify_trace_object_ownership(ctx, objects)


def handle_uploads(session: Session, *, payload: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])
    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝上传清单")

    try:
        objects = normalize_upload_objects(payload["objects"])
    except TraceManifestError as exc:
        raise _manifest_error(exc) from exc

    _verify_objects(ctx, objects, now=now)
    _promote_running(ctx, now=now)
    # The upload manifest is the entry point of the upload grace period: the
    # reaper measures eval_upload_grace_seconds from this phase change.
    lifecycle.mark_status(ctx.attempt, "uploading", now)
    merged = dict(ctx.attempt.artifact_manifest_json or {})
    try:
        merged["objects"] = merge_manifest_objects(merged.get("objects"), objects)
    except TraceManifestError as exc:
        raise _manifest_error(exc) from exc
    ctx.attempt.artifact_manifest_json = merged
    session.commit()
    return {"ok": True, "deduped": False, "status": ctx.attempt.status}


# ---------------------------------------------------------------------------
# completed
# ---------------------------------------------------------------------------


def _ingest_report_and_metrics(
    session: Session,
    *,
    run: EvalRun,
    case_run: EvalCaseRun,
    attempt: EvalCaseAttempt,
    score_manifest: dict,
    settings: Settings | None,
) -> None:
    if settings is None:
        return
    store = object_store_from_settings(settings)
    if store is None:
        return
    try:
        raw = store.get_bytes(score_manifest["object_key"])
        report = json.loads(raw)
    except Exception:  # noqa: BLE001 - scoring must not fail the callback
        # The object was already HEAD-verified; a transport/parse hiccup here
        # must not rewrite the runner's completion status.
        return
    try:
        ingest_score_report(
            session,
            case_run=case_run,
            attempt=attempt,
            report=report,
            object_key=score_manifest["object_key"],
        )
    except Exception as exc:  # noqa: BLE001 - keep the completion auditable
        case_run.result_status = "not_scored"
        case_run.error_summary = f"评分报告解析失败：{type(exc).__name__}"[:1000]
        return
    if run.dataset_version_id:
        definitions = list(
            session.scalars(
                select(MetricDefinition)
                .join(
                    DatasetVersionMetric,
                    DatasetVersionMetric.metric_definition_id == MetricDefinition.id,
                )
                .where(
                    DatasetVersionMetric.dataset_version_id == run.dataset_version_id
                )
            )
        )
        terminal = run.status in ("completed", "partial", "failed", "cancelled")
        recompute_metrics(session, run=run, definitions=definitions, is_final=terminal)


def finalize_completion(
    session: Session,
    *,
    case_run: EvalCaseRun,
    attempt: EvalCaseAttempt,
    execution_status: str,
    result_status: str,
    score_manifest: dict,
    trace_manifest: dict | None,
    summary: dict,
    now: datetime,
    settings: Settings | None = None,
    event_type: str = "case.completed",
    event_payload: dict | None = None,
    sequence: int | None = None,
) -> None:
    """Apply one verified completion to attempt/case/run and commit (M4-02 A2).

    The runner callback and the reconciler's lost-callback backfill share this
    core so the two paths cannot drift. The caller owns object verification and
    event naming: a backfilled completion writes ``case.reconciled_completed``
    so it stays distinguishable from a runner-reported ``case.completed``.
    A ``sequence`` must be supplied for callback semantics; the reconciler lets
    it pick the first free slot.
    """

    run = session.get(EvalRun, case_run.eval_run_id)
    if run is None:  # pragma: no cover - FK guarantees the parent run
        raise CaseNotFound(f"评测不存在：{case_run.eval_run_id}")

    attempt_status = COMPLETION_TO_ATTEMPT_STATUS[execution_status]
    lifecycle.mark_status(attempt, attempt_status, now)
    attempt.ended_at = now
    if attempt.started_at is not None:
        # M8-45 (#307): a completed (or runner-failed) attempt is shard
        # progress, so the still-waiting siblings get a fresh lease before this
        # transaction commits.
        queue_lease.renew_shard_leases(
            session,
            attempt=attempt,
            run=run,
            settings=settings or get_settings(),
            now=now,
        )
    merged = dict(attempt.artifact_manifest_json or {})
    manifests = {"score_manifest": score_manifest}
    if trace_manifest is not None:
        manifest_record = normalize_trace_manifest(trace_manifest)
        manifests["trace_manifest"] = manifest_record
        merged["objects"] = merge_manifest_objects(merged.get("objects"), trace_objects(manifest_record))
    merged.update(manifests)
    attempt.artifact_manifest_json = merged
    case_run.execution_status = execution_status
    case_run.result_status = result_status
    case_run.duration_ms = summary.get("duration_ms")
    if attempt_status != "succeeded":
        case_run.error_code = f"runner_{execution_status}"
    else:
        # 成功完成清掉旧失败 attempt 遗留在 case 行上的诊断字段。
        case_run.error_code = None
        case_run.error_summary = None
        # M8-46 (#311): a completed case proves the current pool works again;
        # restart the consecutive-failure and supply-block streaks.
        spot_fallback.reset_after_success(session, run=run, now=now)
    from .trace_ingestion import has_ingestible_trace_objects, set_case_trace_status

    has_trace_objects = has_ingestible_trace_objects(attempt)
    if trace_manifest is not None:
        trace_status = trace_status_for(manifest_record)
        if trace_status == "ready" and any(
            item.get("artifact_kind") in ("trace", "trace_metadata")
            for item in trace_objects(manifest_record)
        ):
            # Ingestion has not parsed the bytes yet; a fresh callback must not
            # advertise ready before trace_file rows reach a terminal state.
            trace_status = "pending"
    elif has_trace_objects:
        # The upload path can register trace objects in artifact_manifest_json
        # without a separate trace_manifest; those are still ingestible.
        trace_status = "pending"
    else:
        trace_status = "unlinked"
    if trace_status == "unlinked" and has_trace_objects:
        trace_status = "pending"
    set_case_trace_status(
        session, case_run=case_run, attempt=attempt, status=trace_status
    )

    payload = dict(event_payload or {})
    payload.setdefault("execution_status", execution_status)
    payload.setdefault("result_status", result_status)
    payload.setdefault("score_manifest", score_manifest)
    payload.setdefault("trace_status", trace_status)
    if sequence is None:
        sequence = next_free_sequence(session, case_run, attempt)
    _add_event(
        CaseContext(session, case_run, run, attempt),
        event_type=event_type,
        sequence=sequence,
        payload=payload,
    )
    refresh_run_progress(session, run, now=now)
    _ingest_report_and_metrics(
        session,
        run=run,
        case_run=case_run,
        attempt=attempt,
        score_manifest=score_manifest,
        settings=settings,
    )
    # M6-02: enqueue pending trace_file rows in the same completion
    # transaction. Parsing itself is asynchronous and must never delay or
    # fail the callback; a bad manifest only leaves the rows unqueued for the
    # next reconciler pass.
    try:
        from .trace_ingestion import enqueue_attempt

        with session.begin_nested():
            enqueue_attempt(session, attempt, now=now)
    except Exception:  # noqa: BLE001 - ingestion is post-completion work
        logger.exception("enqueue trace ingestion failed for attempt %s", attempt.id)
    # A terminal attempt frees its Runner on every completion path. The
    # failure/spot paths release in their settlement helpers; the success path
    # settles here, and without this the next fixed-pool `started` callback is
    # rejected with runner_busy_conflict (#281).
    lifecycle.release_runner_binding(session, attempt)
    session.commit()


def handle_completed(
    session: Session,
    *,
    payload: dict,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])

    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        if _event_exists(ctx, sequence=payload["sequence"], event_id=payload["event_id"]):
            return {"ok": True, "deduped": True, "status": ctx.attempt.status}
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝完成回调")

    _verify_objects(ctx, [payload["score_manifest"]], now=now, sequence=payload["sequence"])
    trace_manifest = None
    if payload.get("trace_manifest") is not None:
        try:
            trace_manifest = normalize_trace_manifest(payload["trace_manifest"])
        except TraceManifestError as exc:
            raise _manifest_error(exc) from exc
        _verify_objects(
            ctx, trace_objects(trace_manifest), now=now, sequence=payload["sequence"]
        )

    try:
        finalize_completion(
            session,
            case_run=ctx.case_run,
            attempt=ctx.attempt,
            execution_status=payload["execution_status"],
            result_status=payload["result_status"],
            score_manifest=payload["score_manifest"],
            trace_manifest=trace_manifest,
            summary=payload["summary"],
            now=now,
            settings=settings,
            event_type="case.completed",
            event_payload={"event_id": payload["event_id"]},
            sequence=payload["sequence"],
        )
    except TraceManifestError as exc:
        raise _manifest_error(exc) from exc
    return {"ok": True, "deduped": False, "status": ctx.attempt.status}


# ---------------------------------------------------------------------------
# failed
# ---------------------------------------------------------------------------


def build_retry_dispatch(
    session: Session,
    ctx: CaseContext,
    *,
    new_attempt_no: int,
    settings: Settings,
    now: datetime,
    retry_reason: str = "runner_retryable_failure",
) -> Outbox:
    """One single-case shard dispatch for the retry attempt.

    Attempt rows are ONLY created here and in evaluation creation — the
    dispatch loop's crash recovery cannot duplicate them.
    """

    import hashlib
    import json
    from datetime import timedelta
    from urllib.parse import urlencode

    # M8-36: retry manifests must carry every frozen run-wide field. Keep the
    # defaults/source lookups local so this module does not import evaluations
    # at import time (evaluations imports the callback handlers).
    from ..agent_timeout import dispatch_timeout_seconds, job_timeout_minutes
    from .dataset_images import effective_sources, source_records
    from .evaluations import DEFAULT_MAX_TURNS

    run = ctx.run
    retry_shard_id = f"{ctx.attempt.shard_id}-o{ctx.case_run.ordinal}a{new_attempt_no}"[:64]
    dispatch_key = f"{run.id}:{retry_shard_id}:{new_attempt_no}"
    # M8-43 (#304): retries keep the same job/dispatch hierarchy as the first
    # attempt; the manifest must outlive the execute job.
    deadline_seconds = max(
        settings.eval_shard_deadline_minutes * 60,
        dispatch_timeout_seconds(run.case_timeout_seconds),
    )
    deadline = now + timedelta(seconds=deadline_seconds)
    expires_at = now + timedelta(
        seconds=max(settings.eval_shard_ttl_seconds, deadline_seconds)
    )
    runs_on = evaluations.run_runs_on(session, run, settings)
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
        # M8-31/M8-32: retries keep the run's frozen turn cap and Agent budget.
        "max_turns": run.max_turns or DEFAULT_MAX_TURNS,
        "agent_timeout_seconds": (
            run.agent_timeout_seconds or DEFAULT_AGENT_TIMEOUT_SECONDS
        ),
        # M8-33/M8-36: retries must not silently fall back to Docker Hub.
        "image_sources": source_records(effective_sources(session, settings)),
        "runs_on": runs_on,
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
    # M8-51 (#323) option C: retries use the same minimal 1.1 dispatch as the
    # first attempt; digest/expiry stay in the outbox row and are served by
    # the manifest endpoint after the job starts.
    client_payload = {
        "schema_version": "1.1",
        "eval_run_id": run.id,
        "shard_id": retry_shard_id,
        "attempt": new_attempt_no,
        "dispatch_key": dispatch_key,
        "manifest_url": manifest_url,
        "runs_on": runs_on,
        "job_timeout_minutes": job_timeout_minutes(run.case_timeout_seconds),
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
        retry_reason=retry_reason,
    )
    session.add(retry_attempt)
    ctx.case_run.latest_attempt_no = new_attempt_no
    # M9 (#288): every retry — automatic and manual — starts with a fresh,
    # unparsed trace. Without this reset a failed retry that uploaded nothing
    # would keep the previous attempt's terminal badge and could be mistaken
    # for a trace-ready settled case by the early bad-case enqueue.
    ctx.case_run.trace_status = "pending"
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
            "expires_at": expires_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
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
    # ``settings`` stays in the signature for the API edge; the settlement path
    # itself reads the cached Settings like every other service call.
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])

    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        if _event_exists(ctx, sequence=payload["sequence"], event_id=payload["event_id"]):
            return {"ok": True, "deduped": True, "status": ctx.attempt.status}
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝失败回调")

    # M8-43 (#304): a job cancellation uploads a partial trace before its
    # terminal failed callback. Attach the manifest to the attempt so the
    # settlement path can enqueue ingestion; the objects were already checked
    # by the preceding uploads callback.
    if payload.get("trace_manifest") is not None:
        try:
            trace_manifest = normalize_trace_manifest(payload["trace_manifest"])
            trace_items = trace_objects(trace_manifest)
            # The uploads callback normally checks ownership before this
            # terminal callback. Re-check here so a failure callback alone can
            # never attach another attempt's objects.
            _verify_trace_object_ownership(ctx, trace_items)
            merged = dict(ctx.attempt.artifact_manifest_json or {})
            merged["trace_manifest"] = trace_manifest
            merged["objects"] = merge_manifest_objects(
                merged.get("objects"), trace_items
            )
            ctx.attempt.artifact_manifest_json = merged
        except TraceManifestError as exc:
            raise _manifest_error(exc) from exc

    # A scoring-only fault must never rerun the agent (design 06 §6.3): the
    # runner's retryable flag is ignored for that class and the case settles as
    # a terminal failure. Everything else follows the runner's classification.
    retryable = bool(payload["retryable"]) and (
        lifecycle.classify_failure(payload["error_code"]) != lifecycle.SCORING_ONLY
    )
    phase = payload.get("phase")
    if isinstance(phase, str) and phase:
        ctx.attempt.phase = phase[:64]
    retry_queued = lifecycle.settle_attempt_failure(
        session,
        ctx=ctx,
        error_code=payload["error_code"],
        error_summary=payload["error_summary"],
        retryable=retryable,
        event_type="case.failed",
        sequence=payload["sequence"],
        event_payload={
            "event_id": payload["event_id"],
            "error_code": payload["error_code"],
            "retryable": retryable,
            "error_summary": payload["error_summary"][:200],
            "phase": phase,
        },
        now=now,
    )
    return {
        "ok": True,
        "deduped": False,
        "status": ctx.attempt.status,
        "retry_queued": retry_queued,
    }


# ---------------------------------------------------------------------------
# spot interruption (M8-37)
# ---------------------------------------------------------------------------

SPOT_RETRY_LIMIT = 3
SPOT_INTERRUPTED = "spot_interrupted"
SPOT_RETRY_EXHAUSTED = "spot_retry_exhausted"
SPOT_ACTIVE_ATTEMPT_STATUSES = (
    "pending",
    "dispatching",
    "queued",
    "leased",
    "running",
    "scoring",
    "uploading",
)


def _runner_loss_summary(
    prefix: str,
    reason: str,
    *,
    detail: str | None = None,
    provider_state: str | None = None,
    last_heartbeat: str | None = None,
) -> str:
    """Stable, diagnosable text that keeps the real reclaim reason.

    The old spot path collapsed every reclaim into "云侧停止了 spot 实例",
    which hid ``runner_exited`` / ``runner_heartbeat_lost`` and made an
    ephemeral-runner exit look like a provider reclaim (M8-44 #306).
    """

    parts = [f"{prefix}（{reason}）"]
    if provider_state:
        parts.append(f"provider_state={provider_state}")
    if detail:
        parts.append(f"detail={detail}")
    if last_heartbeat:
        parts.append(f"last_heartbeat={last_heartbeat}")
    return "；".join(parts)[:1000]


def handle_runner_lost(
    session: Session,
    *,
    case_run_id: str,
    reason: str,
    detail: str | None = None,
    provider_state: str | None = None,
    last_heartbeat: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Settle a Runner that exited or lost its heartbeat (M8-44 #306).

    The provider instance is still Running; this is not a spot reclaim. The
    attempt settles as a retryable ``runner_lost`` and consumes the run's
    normal ``max_attempts`` budget, not the spot retry budget. Uploaded trace
    objects stay on the attempt for later ingestion.
    """

    now = now or datetime.now(UTC)
    case_run = session.get(EvalCaseRun, case_run_id)
    if case_run is None:
        return False
    run = session.get(EvalRun, case_run.eval_run_id)
    if run is None or run.status in evaluations.TERMINAL_RUN_STATUSES:
        return False
    if run.status in ("cancelling", *evaluations.PAUSED_RUN_STATUSES):
        # Cancellation/budget-pause owns the attempt; a reclaim must not turn
        # it into a normal retry or a misleading "max_attempts exhausted".
        return False
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .where(
                EvalCaseAttempt.eval_case_run_id == case_run.id,
                EvalCaseAttempt.status.in_(SPOT_ACTIVE_ATTEMPT_STATUSES),
            )
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )
    summary = _runner_loss_summary(
        "Runner 退出或失联",
        reason,
        detail=detail,
        provider_state=provider_state,
        last_heartbeat=last_heartbeat,
    )
    retried = False
    for attempt in attempts:
        ctx = CaseContext(session, case_run, run, attempt)
        retried = lifecycle.settle_attempt_failure(
            session,
            ctx=ctx,
            error_code="runner_lost",
            error_summary=summary,
            retryable=True,
            event_type="case.runner_lost",
            event_payload={
                "reason": reason,
                "detail": detail,
                "provider_state": provider_state,
                "last_heartbeat": last_heartbeat,
                "attempt_id": attempt.id,
            },
            now=now,
        ) or retried
    return retried


def handle_spot_interruption(
    session: Session,
    *,
    case_run_id: str,
    reason: str = "provider_stopped",
    detail: str | None = None,
    provider_state: str | None = None,
    last_heartbeat: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Fail the active attempt fast and re-dispatch a new spot attempt (M8-37).

    The provider stopped the instance under a running job. Waiting for the
    300s heartbeat reaper wastes the whole agent window and turns an
    infrastructure event into ``heartbeat_timeout``. This settles the attempt
    as ``spot_interrupted`` and immediately creates the next attempt, capped by
    both ``SPOT_RETRY_LIMIT`` and the run's normal ``max_attempts`` so a run
    never grows beyond its declared attempt budget (M8-44 #306 P2-4).

    Returns True when at least one retry dispatch was queued.
    """

    now = now or datetime.now(UTC)
    case_run = session.get(EvalCaseRun, case_run_id)
    if case_run is None:
        return False
    run = session.get(EvalRun, case_run.eval_run_id)
    if run is None or run.status in evaluations.TERMINAL_RUN_STATUSES:
        return False
    if run.status in ("cancelling", *evaluations.PAUSED_RUN_STATUSES):
        # Cancellation/budget-pause owns the attempt; a reclaim must not turn
        # it into a normal retry or a misleading "max_attempts exhausted".
        return False
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .where(
                EvalCaseAttempt.eval_case_run_id == case_run.id,
                EvalCaseAttempt.status.in_(SPOT_ACTIVE_ATTEMPT_STATUSES),
            )
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )
    summary = _runner_loss_summary(
        "云侧停止了 spot 实例",
        reason,
        detail=detail,
        provider_state=provider_state,
        last_heartbeat=last_heartbeat,
    )
    retried = False
    for attempt in attempts:
        prior = int(
            session.scalar(
                select(func.count())
                .select_from(EvalCaseAttempt)
                .where(
                    EvalCaseAttempt.eval_case_run_id == case_run.id,
                    EvalCaseAttempt.error_code == SPOT_INTERRUPTED,
                )
            )
            or 0
        )
        ctx = CaseContext(session, case_run, run, attempt)
        attempt.status = "failed"
        attempt.ended_at = now
        attempt.error_code = SPOT_INTERRUPTED
        attempt.error_summary = summary
        attempt.retry_reason = "spot_interruption"
        lifecycle.release_runner_binding(session, attempt)
        if attempt.started_at is not None:
            # M8-45 (#307): a started attempt ending (even by spot reclaim) is
            # shard progress; renew the siblings that never started.
            queue_lease.renew_shard_leases(
                session,
                attempt=attempt,
                run=run,
                settings=get_settings(),
                now=now,
            )
        # M8-46 (#311): observe the reclaim before building the retry so a
        # threshold hit switches the run pool first; the retry manifest then
        # targets the fixed/on-demand pool labels instead of spot labels.
        fallback = spot_fallback.record_spot_reclaim(
            session,
            run=run,
            reason=reason,
            now=now,
            settings=get_settings(),
        )
        sequence = next_free_sequence(session, case_run, attempt)
        session.add(
            EvalEvent(
                tenant_id=case_run.tenant_id,
                eval_run_id=run.id,
                eval_case_run_id=case_run.id,
                attempt_no=attempt.attempt_no,
                sequence=sequence,
                event_type="case.spot_interrupted",
                payload_json={
                    "error_code": SPOT_INTERRUPTED,
                    "attempt_no": attempt.attempt_no,
                    "spot_retry_no": prior,
                    "reason": reason,
                    "detail": detail,
                    "provider_state": provider_state,
                    "last_heartbeat": last_heartbeat,
                    "fallback_active": fallback.active,
                    "fallback_activated": fallback.activated,
                    "fallback_to_pool_id": fallback.to_pool_id,
                    "fallback_to_pool_name": fallback.to_pool_name,
                },
            )
        )
        spot_budget_left = prior < SPOT_RETRY_LIMIT
        attempt_budget_left = lifecycle.retry_allowed(run, attempt)
        # A run that has already switched to the fallback pool is no longer
        # spending the spot-retry budget; its retry is only bounded by the
        # normal max_attempts budget. This is what keeps a run from ending as
        # spot_retry_exhausted after the fallback threshold was reached.
        retry_allowed = (spot_budget_left or fallback.active) and attempt_budget_left
        if retry_allowed:
            outbox = build_retry_dispatch(
                session,
                ctx,
                new_attempt_no=attempt.attempt_no + 1,
                settings=get_settings(),
                now=now,
                retry_reason="spot_interruption",
            )
            session.add(outbox)
            case_run.execution_status = "queued"
            case_run.error_code = SPOT_INTERRUPTED
            if fallback.active:
                case_run.error_summary = (
                    f"spot 实例连续被回收 {fallback.failures} 次，"
                    f"{fallback.summary}，已重新派发"
                )
            else:
                case_run.error_summary = "spot 实例被云侧停止，已重新申请竞争态资源池"
            session.add(
                EvalEvent(
                    tenant_id=case_run.tenant_id,
                    eval_run_id=run.id,
                    eval_case_run_id=case_run.id,
                    attempt_no=attempt.attempt_no,
                    sequence=sequence + 1,
                    event_type="case.retry_queued",
                    payload_json={
                        "new_attempt_no": attempt.attempt_no + 1,
                        "spot_retry_no": prior + 1,
                        "reason": "spot_interruption",
                        "fallback_active": fallback.active,
                        "fallback_to_pool_id": fallback.to_pool_id,
                    },
                )
            )
            retried = True
        else:
            case_run.execution_status = "failed"
            if fallback.active:
                case_run.error_code = SPOT_INTERRUPTED
                case_run.error_summary = (
                    f"spot 实例连续被回收 {fallback.failures} 次，"
                    f"{fallback.summary}，但已达到用例重试上限 "
                    f"max_attempts={run.max_attempts}"
                )
            elif not spot_budget_left:
                case_run.error_code = SPOT_RETRY_EXHAUSTED
                case_run.error_summary = (
                    f"spot 实例连续被回收 {SPOT_RETRY_LIMIT} 次，已停止重试"
                )
            else:
                case_run.error_code = SPOT_INTERRUPTED
                case_run.error_summary = (
                    "spot 实例被回收，已达到用例重试上限 "
                    f"max_attempts={run.max_attempts}，已停止重试"
                )
        refresh_run_progress(session, run, now=now)
    if attempts:
        session.commit()
    return retried

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
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import (
    DatasetVersionMetric,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    MetricDefinition,
    Outbox,
)
from ..runner_contracts import validate_payload
from ..scoring.service import ingest_score_report, recompute_metrics
from . import capacity, evaluations, lifecycle
from .evaluations import refresh_run_progress
from .object_store import ObjectMissing, ObjectSizeMismatch, object_store_from_settings
from .trace_manifest import (
    TraceManifestError,
    merge_manifest_objects,
    normalize_trace_manifest,
    normalize_upload_objects,
    trace_objects,
    trace_status_for,
)

ACTIVE_ATTEMPT_STATUSES = evaluations.ACTIVE_ATTEMPT_STATUSES

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
    cap = capacity.effective_concurrency(ctx.session, run, settings=get_settings(), now=now)
    run.effective_concurrency = cap
    # The count below is check-then-act, so serialize concurrent started
    # callbacks by flushing this write first: Postgres takes the eval_run row
    # lock, SQLite takes the database write lock (SELECT ... FOR UPDATE is a
    # no-op on SQLite, so a read lock would not protect the cap).
    ctx.session.flush()
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


def handle_started(session: Session, *, payload: dict, now: datetime | None = None) -> dict:
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
    trace_status = trace_status_for(manifest_record) if trace_manifest is not None else "unlinked"
    case_run.trace_status = trace_status

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

    run = ctx.run
    retry_shard_id = f"{ctx.attempt.shard_id}-o{ctx.case_run.ordinal}a{new_attempt_no}"[:64]
    dispatch_key = f"{run.id}:{retry_shard_id}:{new_attempt_no}"
    deadline = now + timedelta(minutes=settings.eval_shard_deadline_minutes)
    expires_at = now + timedelta(seconds=settings.eval_shard_ttl_seconds)
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
    client_payload = {
        "schema_version": "1.0",
        "eval_run_id": run.id,
        "shard_id": retry_shard_id,
        "attempt": new_attempt_no,
        "dispatch_key": dispatch_key,
        "manifest_url": manifest_url,
        "manifest_digest": manifest_digest,
        "runs_on": runs_on,
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
        retry_reason=retry_reason,
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
    # ``settings`` stays in the signature for the API edge; the settlement path
    # itself reads the cached Settings like every other service call.
    now = now or datetime.now(UTC)
    ctx = resolve_context(session, case_run_id=payload["case_run_id"], attempt_no=payload["attempt"])

    if ctx.attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        if _event_exists(ctx, sequence=payload["sequence"], event_id=payload["event_id"]):
            return {"ok": True, "deduped": True, "status": ctx.attempt.status}
        raise StaleAttempt(f"attempt 处于终态 {ctx.attempt.status}，拒绝失败回调")

    # A scoring-only fault must never rerun the agent (design 06 §6.3): the
    # runner's retryable flag is ignored for that class and the case settles as
    # a terminal failure. Everything else follows the runner's classification.
    retryable = bool(payload["retryable"]) and (
        lifecycle.classify_failure(payload["error_code"]) != lifecycle.SCORING_ONLY
    )
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
        },
        now=now,
    )
    return {
        "ok": True,
        "deduped": False,
        "status": ctx.attempt.status,
        "retry_queued": retry_queued,
    }

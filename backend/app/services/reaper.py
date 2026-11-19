"""Lifecycle reaper: the five execution timeouts (M4-02 A1, design 06 §7).

One sweep covers every active attempt at most once, in a fixed order so the
most specific timeout wins:

1. platform queue  (dispatching/queued,   shard progress lease)
2. agent execution (running,             ``run.case_timeout_seconds``)
3. scoring         (scoring,             ``eval_scoring_timeout_seconds``)
4. upload grace    (uploading,           ``eval_upload_grace_seconds``)
5. heartbeat       (running/scoring/uploading, ``eval_heartbeat_timeout_seconds``)

Rules 2 and 3/4 run before 5 on purpose: a lost agent must be reported as
``execution_timeout``, not merely as a silent heartbeat. Settling goes through
``lifecycle.settle_attempt_failure`` (single transactional path, retry or
terminal), so a timeout can never leave a case in queued/running/scoring/
uploading unless a retry was actually queued.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import EvalCaseAttempt, EvalCaseRun, EvalRun
from . import lifecycle, queue_lease
from .case_attempts import CaseContext


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC for arithmetic."""

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _phase_marker(attempt: EvalCaseAttempt) -> datetime | None:
    """Phase clock with a legacy fallback for rows written before M4-02."""

    return _aware(
        attempt.phase_changed_at or attempt.updated_at or attempt.created_at
    )


def _expired(marker: datetime | None, budget_seconds: int, now: datetime) -> bool:
    if marker is None:
        return False
    return marker + timedelta(seconds=budget_seconds) < now


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="microseconds")


def sweep_lifecycle(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> dict[str, int]:
    """Apply the timeout rules once; returns the number of transitions per class."""

    now = now or datetime.now(UTC)
    limit = int(batch_size if batch_size is not None else settings.eval_lifecycle_batch_size)
    limit = max(1, limit)
    counts = {code: 0 for code in lifecycle.TIMEOUT_CODES}
    processed: set[str] = set()

    def rows(*statuses: str) -> list[tuple[EvalCaseAttempt, EvalCaseRun, EvalRun]]:
        statement = (
            select(EvalCaseAttempt, EvalCaseRun, EvalRun)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .join(EvalRun, EvalRun.id == EvalCaseRun.eval_run_id)
            .where(
                EvalCaseAttempt.status.in_(statuses),
                # Cancellation (A2) owns the attempts of a cancelling/cancelled
                # run: a timeout must not overwrite the cancellation fact.
                EvalRun.status.not_in(("cancelling", "cancelled")),
            )
            .order_by(EvalCaseAttempt.created_at, EvalCaseAttempt.id)
            .limit(limit)
        )
        return list(session.execute(statement).all())

    def expire(
        attempt: EvalCaseAttempt,
        case: EvalCaseRun,
        run: EvalRun,
        *,
        error_code: str,
        summary: str,
        retryable: bool,
        extra_payload: dict | None = None,
    ) -> None:
        if attempt.id in processed:
            return
        processed.add(attempt.id)
        counts[error_code] += 1
        # Heartbeat loss may be retried; a spent queue/execution/scoring/upload
        # budget is terminal for this attempt. Settle still honours max_attempts.
        event_type = (
            "case.failed"
            if retryable and lifecycle.retry_allowed(run, attempt)
            else "case.timeout"
        )
        lifecycle.settle_attempt_failure(
            session,
            ctx=CaseContext(session, case, run, attempt),
            error_code=error_code,
            error_summary=summary,
            retryable=retryable,
            terminal_status="timeout",
            event_type=event_type,
            event_payload={
                "timeout_class": error_code,
                "error_code": error_code,
                "attempt_id": attempt.id,
                **(extra_payload or {}),
            },
            now=now,
        )

    # 1. Platform queue: the shard must show progress (a started attempt) or the
    #    lease runs out. Lease-managed rows read ``queue_deadline``; rows that
    #    predate the lease columns keep the old fixed wall clock from the phase
    #    marker so an in-flight upgrade cannot mass-time-out a queued shard.
    for attempt, case, run in rows("dispatching", "queued"):
        marker = _phase_marker(attempt)
        deadline = _aware(attempt.queue_deadline)
        if deadline is None:
            budget = int(settings.eval_queue_timeout_seconds)
            expired = _expired(marker, budget, now)
        else:
            budget = queue_lease.queue_budget_seconds(run, settings)
            expired = deadline < now
        if not expired:
            continue

        queue_started = _aware(attempt.queue_started_at) or marker
        wait_seconds = (
            int(max(0, (now - queue_started).total_seconds()))
            if queue_started is not None
            else None
        )
        last_progress, shard_started = queue_lease.shard_progress(
            session, run=run, attempt=attempt
        )
        idle_seconds = (
            int(max(0, (now - last_progress).total_seconds()))
            if last_progress is not None
            else wait_seconds
        )
        if wait_seconds is None:
            summary = f"用例排队超时：分片连续无进度超过租约 {budget} 秒"
        else:
            summary = (
                f"用例排队超时：等待 {wait_seconds} 秒未启动，"
                f"最近 {idle_seconds} 秒无进度（租约 {budget} 秒）"
            )
        expire(
            attempt,
            case,
            run,
            error_code=lifecycle.QUEUE_TIMEOUT,
            summary=summary,
            retryable=False,
            extra_payload={
                "queue_wait_seconds": wait_seconds,
                "last_progress_at": _iso(last_progress),
                "queue_started_at": _iso(queue_started),
                "queue_deadline": _iso(attempt.queue_deadline),
                "shard_queue_started_at": _iso(shard_started),
                "lease_budget_seconds": budget,
                "shard_size": int(run.shard_size or 0),
                "concurrency": int(
                    run.effective_concurrency or run.requested_concurrency or 1
                ),
            },
        )

    # 2. Agent execution: the per-run case budget is spent.
    for attempt, case, run in rows("running"):
        started = _aware(attempt.started_at)
        budget = int(run.case_timeout_seconds or settings.eval_execution_timeout_seconds)
        if _expired(started or _phase_marker(attempt), budget, now):
            expire(
                attempt,
                case,
                run,
                error_code=lifecycle.EXECUTION_TIMEOUT,
                summary=f"Agent 执行超时：用例预算 {budget} 秒",
                retryable=False,
            )

    # 3. Scoring stage (a scoring failure must not rerun the agent).
    for attempt, case, run in rows("scoring"):
        if _expired(_phase_marker(attempt), settings.eval_scoring_timeout_seconds, now):
            expire(
                attempt,
                case,
                run,
                error_code=lifecycle.SCORING_TIMEOUT,
                summary=f"评分超时：{settings.eval_scoring_timeout_seconds} 秒内未完成评分",
                retryable=False,
            )

    # 4. Upload grace period: artifacts are still missing after the sandbox hold.
    for attempt, case, run in rows("uploading"):
        if _expired(_phase_marker(attempt), settings.eval_upload_grace_seconds, now):
            expire(
                attempt,
                case,
                run,
                error_code=lifecycle.UPLOAD_TIMEOUT,
                summary=f"上传宽限期超时：{settings.eval_upload_grace_seconds} 秒内未完成上传",
                retryable=False,
            )

    # 5. Heartbeat loss (last: rules 2-4 already claimed their attempts).
    for attempt, case, run in rows("running", "scoring", "uploading"):
        heartbeat = _aware(attempt.heartbeat_at) or _aware(attempt.started_at)
        if _expired(heartbeat, settings.eval_heartbeat_timeout_seconds, now):
            expire(
                attempt,
                case,
                run,
                error_code=lifecycle.HEARTBEAT_TIMEOUT,
                summary=(
                    f"心跳超时：{settings.eval_heartbeat_timeout_seconds} 秒内未收到 Runner 心跳"
                ),
                retryable=True,
            )

    return counts

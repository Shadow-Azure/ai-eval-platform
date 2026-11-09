"""Lifecycle reaper: the five execution timeouts (M4-02 A1, design 06 §7).

One sweep covers every active attempt at most once, in a fixed order so the
most specific timeout wins:

1. platform queue  (dispatching/queued,   ``eval_queue_timeout_seconds``)
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
from . import lifecycle
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
            },
            now=now,
        )

    # 1. Platform queue: the shard never produced a started callback.
    for attempt, case, run in rows("dispatching", "queued"):
        if _expired(_phase_marker(attempt), settings.eval_queue_timeout_seconds, now):
            expire(
                attempt,
                case,
                run,
                error_code=lifecycle.QUEUE_TIMEOUT,
                summary=(
                    f"用例排队超时：{settings.eval_queue_timeout_seconds} 秒内未开始执行"
                ),
                retryable=False,
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

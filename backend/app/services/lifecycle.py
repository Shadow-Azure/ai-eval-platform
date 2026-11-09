"""Execution lifecycle primitives shared by callbacks, reaper and cancellation (M4-02).

This module owns the parts of the attempt state machine that are not tied to a
single runner callback:

- the five stable timeout codes and the failure classification (design 06 §6.3
  and §7: environment/transport faults retry, configuration faults fail fast,
  scoring faults must not rerun the agent);
- ``mark_status`` — the only place that moves an attempt between phases, so
  ``phase_changed_at`` cannot drift from ``status``;
- ``release_runner_binding`` — a terminal attempt frees its runner slot;
- ``settle_attempt_failure`` — the ONE transactional settlement path for a
  failed/timed-out attempt (terminal state, event, optional retry dispatch,
  case/run rollup). ``case_attempts.handle_failed`` and the lifecycle reaper
  both go through it.

``case_attempts`` imports this module at import time, so the dependency back to
it (``CaseContext`` type and ``build_retry_dispatch``) is deliberately lazy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import EvalCaseAttempt, EvalEvent, EvalRun
from . import evaluations
from .evaluations import refresh_run_progress

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids an import cycle)
    from .case_attempts import CaseContext

# Stable timeout codes (issue #79 acceptance: one diagnosable code per class).
QUEUE_TIMEOUT = "queue_timeout"
EXECUTION_TIMEOUT = "execution_timeout"
SCORING_TIMEOUT = "scoring_timeout"
UPLOAD_TIMEOUT = "upload_timeout"
HEARTBEAT_TIMEOUT = "heartbeat_timeout"
TIMEOUT_CODES = (
    QUEUE_TIMEOUT,
    EXECUTION_TIMEOUT,
    SCORING_TIMEOUT,
    UPLOAD_TIMEOUT,
    HEARTBEAT_TIMEOUT,
)

# Timeout terminal case status: execution classes end the case as ``timeout``;
# a scoring timeout is a failed scoring stage, not a timed-out agent run.
_TIMEOUT_CASE_STATUS = {
    QUEUE_TIMEOUT: "timeout",
    EXECUTION_TIMEOUT: "timeout",
    UPLOAD_TIMEOUT: "timeout",
    HEARTBEAT_TIMEOUT: "timeout",
}

# Environment/transport faults: another attempt may succeed (design 06 §6.3).
RETRYABLE_FAILURE_CODES = frozenset(
    {
        HEARTBEAT_TIMEOUT,
        "spot_interrupted",
        "runner_lost",
        "network_error",
        "model_rate_limited",
        "model_unavailable",
        "upload_failed",
        "callback_retryable",
        "service_unavailable",
    }
)
# Deterministic faults: retrying only burns budget.
PERMANENT_FAILURE_CODES = frozenset(
    {
        "config_error",
        "credential_forbidden",
        "credential_unavailable",
        "commit_missing",
        "asset_invalid",
        "validation_error",
        "callback_permanent",
        "dispatch_failed",
    }
)
# Scoring-stage faults: fail the case without rerunning the agent.
SCORING_ONLY_FAILURE_CODES = frozenset(
    {
        "scoring_failed",
        SCORING_TIMEOUT,
        "artifact_missing",
    }
)

_RETRYABLE_PREFIXES = ("network_", "model_5", "spot_", "upload_")
_PERMANENT_PREFIXES = ("config_", "credential_", "commit_", "asset_", "validation_")

RETRYABLE = "retryable"
PERMANENT = "permanent"
SCORING_ONLY = "scoring_only"

# Run states that must never spawn a new attempt (A2 sets ``cancelling``).
_RUN_RETRY_BLOCKING_STATUSES = (*evaluations.TERMINAL_RUN_STATUSES, "cancelling")


def classify_failure(error_code: str | None) -> str:
    """Map a failure code to ``retryable`` / ``permanent`` / ``scoring_only``.

    Exact codes win, then the transport prefixes, then the deterministic
    prefixes, and anything unknown defaults to ``permanent`` (fail fast).
    """

    code = (error_code or "").strip()
    if code in SCORING_ONLY_FAILURE_CODES:
        return SCORING_ONLY
    if code in RETRYABLE_FAILURE_CODES:
        return RETRYABLE
    if code in PERMANENT_FAILURE_CODES:
        return PERMANENT
    if code.startswith(_RETRYABLE_PREFIXES):
        return RETRYABLE
    if code.startswith(_PERMANENT_PREFIXES):
        return PERMANENT
    return PERMANENT


def mark_status(attempt: EvalCaseAttempt, status: str, now: datetime) -> None:
    """Move an attempt to ``status`` and stamp the phase clock."""

    attempt.status = status
    attempt.phase_changed_at = now


def release_runner_binding(session: Session, attempt: EvalCaseAttempt) -> None:
    """Free the runner that was bound to this attempt.

    Same effect as ``runner_service.release_case`` (online, binding cleared,
    heartbeat refreshed) but without its intermediate commit, so the caller's
    settlement stays one transaction.
    """

    if not attempt.runner_id:
        return
    from ..models import Runner  # local import keeps models import order simple

    runner = session.get(Runner, attempt.runner_id)
    if runner is None:
        return
    if runner.busy_case_attempt_id != attempt.eval_case_run_id:
        return
    runner.busy_case_attempt_id = None
    runner.status = "online"
    runner.last_heartbeat_at = attempt.ended_at or datetime.now(UTC)


def retry_allowed(run: EvalRun, attempt: EvalCaseAttempt) -> bool:
    """Whether another attempt is both configured and still meaningful."""

    return (
        attempt.attempt_no + 1 <= run.max_attempts
        and run.status not in _RUN_RETRY_BLOCKING_STATUSES
    )


def terminal_case_status(error_code: str) -> str:
    """Case status for a terminal settlement of ``error_code``."""

    return _TIMEOUT_CASE_STATUS.get(error_code, "failed")


def _event_id_recorded(session: Session, ctx: CaseContext, event_id: str) -> bool:
    statement = select(EvalEvent.payload_json).where(
        EvalEvent.eval_case_run_id == ctx.case_run.id,
        EvalEvent.attempt_no == ctx.attempt.attempt_no,
    )
    return any(
        (payload or {}).get("event_id") == event_id
        for payload in session.scalars(statement)
    )


def _next_sequence(session: Session, ctx: CaseContext) -> int:
    """First sequence number free for this (case, attempt) row.

    The natural key is (case_run, attempt_no, sequence), so a reaper event must
    not collide with the started (0) or last heartbeat sequence.
    """

    highest = int(
        session.scalar(
            select(func.max(EvalEvent.sequence)).where(
                EvalEvent.eval_case_run_id == ctx.case_run.id,
                EvalEvent.attempt_no == ctx.attempt.attempt_no,
            )
        )
        or 0
    )
    return max(highest, int(ctx.attempt.sequence or 0)) + 1


def _add_attempt_event(
    session: Session, ctx: CaseContext, *, event_type: str, sequence: int, payload: dict
) -> None:
    session.add(
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


def settle_attempt_failure(
    session: Session,
    *,
    ctx: CaseContext,
    error_code: str,
    error_summary: str,
    retryable: bool,
    terminal_status: str = "failed",
    event_type: str = "case.failed",
    sequence: int | None = None,
    event_payload: dict | None = None,
    now: datetime,
) -> bool:
    """Settle one failed/timed-out attempt transactionally.

    Contract:
    - ``ctx`` is a resolved ``case_attempts.CaseContext`` (session/case/run/attempt).
    - ``error_code`` is stored verbatim on the attempt and the case; the caller
      owns classification (``classify_failure``) and passes the effective
      ``retryable`` flag. Scoring-only faults must arrive retryable=False.
    - ``terminal_status`` is the attempt status (``failed`` by default, the
      reaper passes ``timeout``); the case status is derived from the code
      (queue/execution/upload/heartbeat → ``timeout``, else ``failed``).
    - ``event_type`` defaults to ``case.failed``; the reaper uses
      ``case.timeout`` (or ``case.failed`` when the loss is retried).
      ``event_payload`` is merged into the event payload (``timeout_class``,
      ``attempt_id``...) and may carry the callback ``event_id`` for dedupe.
    - ``sequence=None`` picks the first free sequence for the attempt.
    - Commits. Returns True iff a retry attempt + dispatch outbox row was
      queued; False for a terminal settlement or an already-terminal attempt
      (replayed event → no-op, so callers can retry safely).
    """

    attempt = ctx.attempt
    if attempt.status in evaluations.TERMINAL_ATTEMPT_STATUSES:
        return False

    payload = dict(event_payload or {})
    event_id = payload.get("event_id")
    if event_id is not None and _event_id_recorded(session, ctx, str(event_id)):
        return False
    if sequence is None:
        sequence = _next_sequence(session, ctx)
    payload.update(
        {
            "error_code": error_code,
            "retryable": retryable,
            "error_summary": error_summary[:200],
        }
    )
    if event_id is None:
        payload.pop("event_id", None)

    mark_status(attempt, terminal_status, now)
    attempt.ended_at = now
    attempt.error_code = error_code
    attempt.error_summary = error_summary
    attempt.retry_reason = RETRYABLE if retryable else PERMANENT
    release_runner_binding(session, attempt)
    _add_attempt_event(session, ctx, event_type=event_type, sequence=sequence, payload=payload)

    retried = False
    new_attempt_no = attempt.attempt_no + 1
    if retryable and retry_allowed(ctx.run, attempt):
        from . import case_attempts  # local import: case_attempts imports us

        outbox_row = case_attempts.build_retry_dispatch(
            session,
            ctx,
            new_attempt_no=new_attempt_no,
            settings=get_settings(),
            now=now,
        )
        session.add(outbox_row)
        ctx.case_run.execution_status = "queued"
        ctx.case_run.error_code = error_code
        _add_attempt_event(
            session,
            ctx,
            event_type="case.retry_queued",
            sequence=sequence + 1,
            payload={
                "new_attempt_no": new_attempt_no,
                "shard_id": outbox_row.payload_json["shard_id"],
                "dispatch_key": outbox_row.dispatch_key,
            },
        )
        retried = True
    else:
        if ctx.case_run.execution_status not in evaluations.TERMINAL_CASE_STATUSES:
            ctx.case_run.execution_status = terminal_case_status(error_code)
        ctx.case_run.error_code = error_code
        ctx.case_run.error_summary = error_summary[:1000]

    refresh_run_progress(session, ctx.run, now=now)
    session.commit()
    return retried

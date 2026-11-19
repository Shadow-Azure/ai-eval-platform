"""Shard progress lease for the platform queue timeout (issue #307, design 06 §7).

The platform marks every attempt of a shard ``queued`` at dispatch time, but a
shard usually runs in several waves: only ``effective_concurrency`` Jobs start
at once and the rest wait for a free Runner. A fixed queue wall clock kills the
later waves from a single dispatch instant. The lease fixes that by measuring a
*shard-level* progress budget instead:

- the initial budget is ``case_timeout + eval_queue_timeout_grace_seconds``,
  capped by ``eval_queue_timeout_max_seconds``;
- any attempt that ``started`` (or a started attempt that reached a terminal
  state) renews the deadline of the still-waiting attempts of the same shard;
- the deadline can never move past the shard's first queue time plus the
  absolute ceiling, so a completely stuck shard still times out.

Heartbeats deliberately do not renew: they prove a process is alive, not that
the shard is advancing, so a zombie shard cannot extend itself forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import EvalCaseAttempt, EvalCaseRun, EvalRun

# Attempts that have not started the agent yet. ``leased`` is included for the
# older M2 state machine; the reaper still treats ``dispatching``/``queued`` as
# the queue classes.
WAITING_ATTEMPT_STATUSES = ("pending", "dispatching", "queued", "leased")


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC for arithmetic."""

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _first_stamp(row: EvalCaseAttempt) -> datetime | None:
    """Earliest clock that says when this attempt entered the queue."""

    return _aware(row.queue_started_at or row.phase_changed_at or row.created_at)


def queue_budget_seconds(run: EvalRun, settings: Settings) -> int:
    """Per-renewal budget: the case budget plus a grace period, capped."""

    case_timeout = int(run.case_timeout_seconds or settings.eval_execution_timeout_seconds or 1)
    grace = max(0, int(settings.eval_queue_timeout_grace_seconds))
    ceiling = max(1, int(settings.eval_queue_timeout_max_seconds))
    return max(1, min(case_timeout + grace, ceiling))


def stamp_dispatch_lease(
    attempt: EvalCaseAttempt,
    *,
    run: EvalRun,
    settings: Settings,
    now: datetime,
) -> None:
    """Write the initial lease when a shard is actually dispatched to GitHub."""

    attempt.queue_started_at = now
    attempt.queue_deadline = now + timedelta(seconds=queue_budget_seconds(run, settings))


def _shard_attempts(
    session: Session, *, run: EvalRun, attempt: EvalCaseAttempt
) -> list[EvalCaseAttempt]:
    if not attempt.shard_id:
        return []
    return list(
        session.scalars(
            select(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseAttempt.shard_id == attempt.shard_id,
                EvalCaseAttempt.attempt_no == attempt.attempt_no,
            )
        )
    )


def renew_shard_leases(
    session: Session,
    *,
    attempt: EvalCaseAttempt,
    run: EvalRun,
    settings: Settings,
    now: datetime,
) -> int:
    """Extend the lease of every still-waiting sibling in the same shard.

    One SQL ``UPDATE`` covers the whole shard (S ≤ 255). Legacy rows without
    ``queue_started_at`` use their phase clock as the shard start, so an
    in-flight upgrade keeps a coherent absolute ceiling.
    """

    rows = _shard_attempts(session, run=run, attempt=attempt)
    siblings = [
        row
        for row in rows
        if row.id != attempt.id and row.status in WAITING_ATTEMPT_STATUSES
    ]
    if not siblings:
        return 0

    starts = [stamp for stamp in (_first_stamp(row) for row in rows) if stamp is not None]
    if not starts:
        return 0
    shard_started_at = min(starts)
    budget = queue_budget_seconds(run, settings)
    ceiling = max(1, int(settings.eval_queue_timeout_max_seconds))
    deadline = min(
        now + timedelta(seconds=budget),
        shard_started_at + timedelta(seconds=ceiling),
    )

    session.execute(
        update(EvalCaseAttempt)
        .where(EvalCaseAttempt.id.in_([row.id for row in siblings]))
        .values(queue_deadline=deadline)
    )
    return len(siblings)


def shard_progress(
    session: Session, *, run: EvalRun, attempt: EvalCaseAttempt
) -> tuple[datetime | None, datetime | None]:
    """Return ``(last_progress_at, shard_queue_started_at)`` for diagnostics.

    Progress is the latest ``started_at``/``ended_at`` of any attempt in the
    shard. A shard that never produced a ``started`` callback has no progress;
    the queue start (dispatch time) is still reported as the baseline.
    """

    rows = _shard_attempts(session, run=run, attempt=attempt)
    last_progress: datetime | None = None
    queue_started: datetime | None = None
    for row in rows:
        started = _aware(row.started_at)
        ended = _aware(row.ended_at)
        for stamp in (started, ended):
            if stamp is not None and (last_progress is None or stamp > last_progress):
                last_progress = stamp
        stamp = _first_stamp(row)
        if stamp is not None and (queue_started is None or stamp < queue_started):
            queue_started = stamp
    return last_progress, queue_started

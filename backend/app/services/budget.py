"""Model-budget pause and resume (M4-05, design 06 §8).

The gateway is the authority on model spend. When the authoritative sum of
``model_usage.cost_cny`` reaches ``eval_run.model_budget_cny`` the platform
does not fail the evaluation:

1. the run enters ``pausing_budget`` and the scheduler stops releasing new
   shards/attempts (pending dispatches are parked as ``cancelled``);
2. the gateway answers ``402 budget_paused`` and refuses new model calls, so a
   still-running agent cannot keep spending;
3. attempts that never started are settled immediately; attempts that are
   already ``running/scoring/uploading`` may upload their artifacts and settle
   through their callbacks (the run-level reconciler force-settles them after
   ``eval_budget_pause_grace_seconds``);
4. once no active attempt remains the run becomes ``paused_budget`` (still
   non-terminal: no final metrics are written);
5. ``resume_budget`` atomically adds budget, bumps ``budget_version``, writes
   the audit trail and creates new attempts only for unfinished cases.

Every function here is idempotent: pause/event writes dedupe on the run, and a
replayed/parallel resume with a stale ``budget_version`` is rejected without
adding budget twice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox
from ..money import Money, MoneyError, money_str
from . import evaluations, identity, lifecycle

# ``eval_run.model_budget_cny`` is Numeric(12, 6); refuse a resume that would
# overflow the column instead of failing at commit time.
MAX_BUDGET_CNY = Decimal("999999.999999")

PAUSE_EVENT = "eval.budget_pausing"
PAUSED_EVENT = "eval.budget_paused"
RESUMED_EVENT = "eval.budget_resumed"

# Attempt states that never started an agent (safe to settle at pause entry).
NOT_STARTED_ATTEMPT_STATUSES = ("pending", "dispatching", "queued")


class BudgetError(Exception):
    """Base for resume rejections mapped to HTTP errors by the API layer."""


class BudgetNotPaused(BudgetError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"评测不处于预算暂停状态（{status}）")


class BudgetPauseInProgress(BudgetError):
    def __init__(self) -> None:
        super().__init__("评测正在收口活跃用例，稍后再增加预算")


class BudgetVersionConflict(BudgetError):
    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__(f"预算版本已变化（当前 {current_version}），请刷新后重试")


class BudgetInvalid(BudgetError):
    pass


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _budget_events(session: Session, run: EvalRun, event_type: str) -> list[EvalEvent]:
    return list(
        session.scalars(
            select(EvalEvent)
            .where(
                EvalEvent.eval_run_id == run.id,
                EvalEvent.event_type == event_type,
                EvalEvent.eval_case_run_id.is_(None),
            )
            .order_by(EvalEvent.id)
        )
    )


def _append_budget_event(
    session: Session, run: EvalRun, event_type: str, payload: dict
) -> EvalEvent:
    """Append one event per (run, event_type, budget_version).

    ``evaluations.append_run_event`` dedupes by run/event type, which is right
    for one-shot lifecycle events but wrong for repeated pause/resume cycles:
    each cycle bumps ``budget_version`` and must be visible in the timeline.
    """

    version = payload.get("budget_version")
    for event in _budget_events(session, run, event_type):
        if event.payload_json.get("budget_version") == version:
            return event
    event = EvalEvent(
        tenant_id=run.tenant_id,
        eval_run_id=run.id,
        event_type=event_type,
        payload_json=payload,
    )
    session.add(event)
    return event


def _lock_run(session: Session, run: EvalRun) -> EvalRun | None:
    """Re-read the run with a row lock so concurrent pauses/finalizes serialize.

    SQLite ignores ``FOR UPDATE`` (single-writer anyway); PostgreSQL makes the
    loser of a pause/finalize race observe the committed state and no-op.
    """

    return session.scalar(
        select(EvalRun).where(EvalRun.id == run.id).with_for_update()
    )


def _set_case_unfinished(case: EvalCaseRun, error_code: str | None = None) -> None:
    if case.execution_status not in evaluations.TERMINAL_CASE_STATUSES:
        case.execution_status = "queued"
    if error_code:
        case.error_code = error_code


def _settle_not_started(
    session: Session, run: EvalRun, *, now: datetime
) -> int:
    """Settle attempts that never started an agent; their cases stay unfinished."""

    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseAttempt.status.in_(NOT_STARTED_ATTEMPT_STATUSES),
            )
        )
    )
    for attempt in attempts:
        lifecycle.mark_status(attempt, "cancelled", now)
        attempt.ended_at = now
        attempt.error_code = evaluations.BUDGET_PAUSED
        attempt.error_summary = "模型预算用尽，暂停收口；恢复后重新调度"
        case = session.get(EvalCaseRun, attempt.eval_case_run_id)
        if case is not None:
            _set_case_unfinished(case, evaluations.BUDGET_PAUSED)
    return len(attempts)


def _park_pending_dispatches(session: Session, run: EvalRun) -> int:
    """Cancel not-yet-sent shard dispatches; resume builds fresh ones."""

    rows = list(
        session.scalars(
            select(Outbox).where(
                Outbox.topic == evaluations.SHARD_DISPATCH_TOPIC,
                Outbox.aggregate_id == run.id,
                Outbox.status.in_(("pending", "processing")),
            )
        )
    )
    for row in rows:
        row.payload_json = {
            **row.payload_json,
            "last_error": evaluations.BUDGET_PAUSED,
        }
        row.status = "cancelled"
    return len(rows)


def pause_for_budget(
    session: Session,
    run: EvalRun,
    *,
    spent: Decimal | None = None,
    now: datetime | None = None,
    source: str = "model_gateway",
) -> bool:
    """Enter ``pausing_budget`` once the authoritative spend reached the budget.

    Returns False when the run is already past the pause point (or has no
    budget); the caller can retry safely.
    """

    now = _now(now)
    locked = _lock_run(session, run)
    if locked is None:
        return False
    run = locked
    if run.status not in ("queued", "running"):
        return False
    if run.model_budget_cny is None:
        return False
    spent = spent if spent is not None else evaluations.spent_cny(session, run.id)
    if Money.of(spent) < Money.of(run.model_budget_cny):
        return False

    run.status = "pausing_budget"
    run.pause_reason = evaluations.BUDGET_EXCEEDED
    run.budget_spent_cny = spent
    run.queue_reason = None
    session.flush()

    _settle_not_started(session, run, now=now)
    _park_pending_dispatches(session, run)
    _append_budget_event(
        session,
        run,
        PAUSE_EVENT,
        {
            "reason": evaluations.BUDGET_EXCEEDED,
            "source": source,
            "model_budget_cny": money_str(run.model_budget_cny),
            "budget_spent_cny": money_str(spent),
            "budget_version": run.budget_version,
        },
    )
    session.flush()
    evaluations.refresh_run_progress(session, run, now=now)
    session.commit()
    return True


def finalize_paused(session: Session, run: EvalRun, *, now: datetime) -> bool:
    """Move ``pausing_budget`` → ``paused_budget`` once no active attempt is left."""

    if run.status != "pausing_budget":
        return False
    locked = _lock_run(session, run)
    if locked is None or locked.status != "pausing_budget":
        return False
    run = locked
    if evaluations.active_attempts(session, run):
        return False
    run.status = "paused_budget"
    session.flush()
    evaluations.refresh_run_progress(session, run, now=now)
    _append_budget_event(
        session,
        run,
        PAUSED_EVENT,
        {
            "reason": evaluations.BUDGET_EXCEEDED,
            "model_budget_cny": money_str(run.model_budget_cny),
            "budget_spent_cny": money_str(run.budget_spent_cny),
            "budget_version": run.budget_version,
            "finished_count": max(
                0, int(run.total_cases or 0) - int(run.queued_count or 0) - int(run.running_count or 0)
            ),
            "unfinished_count": int(run.queued_count or 0) + int(run.running_count or 0),
        },
    )
    session.commit()
    return True


def _force_settle_active(session: Session, run: EvalRun, *, now: datetime) -> int:
    """Grace expired: the interrupted attempts must not keep the pause open."""

    attempts = evaluations.active_attempts(session, run)
    for attempt in attempts:
        lifecycle.mark_status(attempt, "cancelled", now)
        attempt.ended_at = now
        attempt.error_code = evaluations.BUDGET_PAUSED
        attempt.error_summary = "预算暂停宽限期结束，等待恢复后重新执行"
        lifecycle.release_runner_binding(session, attempt)
        case = session.get(EvalCaseRun, attempt.eval_case_run_id)
        if case is not None:
            _set_case_unfinished(case, evaluations.BUDGET_PAUSED)
    if attempts:
        session.flush()
        evaluations.refresh_run_progress(session, run, now=now)
    return len(attempts)


def _pausing_since(session: Session, run: EvalRun, now: datetime) -> datetime:
    for event in reversed(_budget_events(session, run, PAUSE_EVENT)):
        if event.payload_json.get("budget_version") != run.budget_version:
            continue
        value = _aware(event.occurred_at)
        if value is not None:
            return value
    return _aware(run.updated_at) or now


def reconcile_budget_pauses(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> dict[str, int]:
    """Worker pass: detect over-budget runs and settle ``pausing_budget`` ones."""

    now = _now(now)
    limit = max(1, int(batch_size or settings.eval_lifecycle_batch_size))
    counts = {"budget_paused": 0, "budget_settled": 0}

    active = list(
        session.scalars(
            select(EvalRun)
            .where(
                EvalRun.status.in_(("queued", "running")),
                EvalRun.model_budget_cny.is_not(None),
            )
            .order_by(EvalRun.created_at, EvalRun.id)
            .limit(limit)
        )
    )
    for run in active:
        spent = evaluations.spent_cny(session, run.id)
        if run.budget_spent_cny != spent:
            run.budget_spent_cny = spent
        if Money.of(spent) >= Money.of(run.model_budget_cny or 0):
            if pause_for_budget(session, run, spent=spent, now=now, source="reconciler"):
                counts["budget_paused"] += 1
    session.commit()

    pausing = list(
        session.scalars(
            select(EvalRun)
            .where(EvalRun.status == "pausing_budget")
            .order_by(EvalRun.created_at, EvalRun.id)
            .limit(limit)
        )
    )
    grace = int(settings.eval_budget_pause_grace_seconds)
    for run in pausing:
        run.budget_spent_cny = evaluations.spent_cny(session, run.id)
        if evaluations.active_attempts(session, run):
            if _pausing_since(session, run, now) + timedelta(seconds=grace) < now:
                _force_settle_active(session, run, now=now)
            else:
                session.commit()
                continue
        if finalize_paused(session, run, now=now):
            counts["budget_settled"] += 1

    paused = list(
        session.scalars(
            select(EvalRun)
            .where(EvalRun.status == "paused_budget")
            .order_by(EvalRun.created_at, EvalRun.id)
            .limit(limit)
        )
    )
    for run in paused:
        # Late usage can still land (a stream started before the pause). Keep
        # the snapshot honest without re-opening the pause.
        run.budget_spent_cny = evaluations.spent_cny(session, run.id)
        session.commit()

    return counts


def _unfinished_cases(session: Session, run: EvalRun) -> list[EvalCaseRun]:
    return list(
        session.scalars(
            select(EvalCaseRun)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseRun.execution_status.in_(("queued", "running")),
            )
            .order_by(EvalCaseRun.ordinal)
        )
    )


def resume_budget(
    session: Session,
    *,
    run_id: str,
    additional_budget_cny: Decimal | float | str,
    expected_budget_version: int,
    actor_user_id: str | None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[EvalRun, int]:
    """Atomically add budget and re-dispatch only the unfinished cases."""

    settings = settings or get_settings()
    now = _now(now)
    run = session.get(EvalRun, run_id, with_for_update=True)
    if run is None:
        raise evaluations.EvaluationNotFound(run_id)

    if run.status == "pausing_budget":
        raise BudgetPauseInProgress()
    if run.status != "paused_budget":
        # A replay of a successful resume has already bumped the version; the
        # client's stale version must not add budget a second time.
        if int(run.budget_version or 1) > int(expected_budget_version):
            raise BudgetVersionConflict(int(run.budget_version or 1))
        raise BudgetNotPaused(run.status)
    if int(run.budget_version or 1) != int(expected_budget_version):
        raise BudgetVersionConflict(int(run.budget_version or 1))

    try:
        additional = Money.of(additional_budget_cny)
    except MoneyError as exc:
        raise BudgetInvalid("additional_budget_cny 必须是数字") from exc
    if additional <= 0:
        raise BudgetInvalid("additional_budget_cny 必须大于 0")
    if run.model_budget_cny is None:
        raise BudgetInvalid("评测没有模型预算，无法增加")
    if Money.of(run.model_budget_cny) + additional > Money.of(MAX_BUDGET_CNY):
        raise BudgetInvalid("增加后的预算超过上限 999999.999999")

    spent = evaluations.spent_cny(session, run.id)
    previous_budget = Money.of(run.model_budget_cny)
    previous_version = int(run.budget_version or 1)

    run.model_budget_cny = (previous_budget + additional).amount
    run.budget_spent_cny = spent
    run.budget_version = previous_version + 1
    run.pause_reason = None
    run.status = "queued"
    run.queue_reason = None
    run.effective_concurrency = None
    run.finished_at = None

    unfinished = _unfinished_cases(session, run)
    attempts_created = 0
    grouped: dict[int, list[EvalCaseRun]] = {}
    for case in unfinished:
        if case.execution_status == "running":
            case.execution_status = "queued"
        case.error_code = None
        case.error_summary = None
        new_attempt_no = max(1, int(case.latest_attempt_no or 0)) + 1
        grouped.setdefault(new_attempt_no, []).append(case)

    for attempt_no in sorted(grouped):
        bundle = evaluations.build_case_dispatches(
            session,
            run=run,
            case_runs=grouped[attempt_no],
            attempt_no=attempt_no,
            shard_size=max(1, int(run.shard_size or 1)),
            shard_prefix=f"resume_b{run.budget_version}_a{attempt_no}",
            settings=settings,
            now=now,
        )
        session.add_all(bundle.outbox_rows)
        session.add_all(bundle.attempts)
        attempts_created += len(bundle.attempts)

    identity.record_audit(
        session,
        tenant_id=run.tenant_id,
        actor_user_id=actor_user_id,
        action="eval.budget_resumed",
        target_type="eval_run",
        target_id=run.id,
        metadata={
            "previous_budget_cny": previous_budget.as_str(),
            "additional_budget_cny": additional.as_str(),
            "model_budget_cny": money_str(run.model_budget_cny),
            "budget_spent_cny": money_str(spent),
            "budget_version": run.budget_version,
            "unfinished_cases": len(unfinished),
            "attempts_created": attempts_created,
        },
    )
    _append_budget_event(
        session,
        run,
        RESUMED_EVENT,
        {
            "previous_budget_cny": previous_budget.as_str(),
            "additional_budget_cny": additional.as_str(),
            "model_budget_cny": money_str(run.model_budget_cny),
            "budget_spent_cny": money_str(spent),
            "budget_version": run.budget_version,
            "unfinished_cases": len(unfinished),
            "attempts_created": attempts_created,
        },
    )

    session.flush()
    evaluations.refresh_run_progress(session, run, now=now)
    session.commit()
    return run, attempts_created

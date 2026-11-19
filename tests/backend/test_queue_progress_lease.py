"""M8-45 (#307): shard progress lease instead of a fixed queue wall clock.

The acceptance boundaries are driven by injected ``now`` values:

- a dispatched shard gets ``queue_started_at`` and ``queue_deadline``;
- a ``started`` attempt (and a started attempt reaching a terminal state)
  renews every still-waiting sibling of the shard;
- a queue timeout itself must not renew the shard (otherwise a stuck shard
  would keep renewing itself);
- renewal is capped at the shard's first queue time + 23h;
- legacy rows without ``queue_deadline`` keep the old fixed fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun
from app.seed import ensure_default_scope
from app.services import case_attempts, dispatcher, evaluations, lifecycle, queue_lease, reaper
from sqlalchemy import select
from sqlalchemy.orm import Session


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _seconds(value: datetime, reference: datetime) -> float:
    return (value - reference).total_seconds()


@pytest.fixture
def lease_settings(configure_settings):
    """Small clocks: case budget 60s + 10s grace = 70s lease, 300s ceiling."""

    configure_settings(
        EVAL_QUEUE_TIMEOUT_SECONDS=60,
        EVAL_QUEUE_TIMEOUT_GRACE_SECONDS=10,
        EVAL_QUEUE_TIMEOUT_MAX_SECONDS=300,
        EVAL_LIFECYCLE_BATCH_SIZE=50,
    )


def _make_run(
    session: Session,
    *,
    key: str,
    case_keys: tuple[str, ...] = ("case-a", "case-b", "case-c"),
    case_timeout_seconds: int = 60,
) -> EvalRun:
    ensure_default_scope(session)
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="排队租约",
        case_keys=list(case_keys),
        case_timeout_seconds=case_timeout_seconds,
        idempotency_key=key,
        created_by="agent",
        settings=get_settings(),
    )
    session.commit()
    return run


def _attempt_rows(session: Session, run: EvalRun) -> list[EvalCaseAttempt]:
    session.expire_all()
    return list(
        session.scalars(
            select(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseAttempt.eval_case_run_id, EvalCaseAttempt.attempt_no)
        )
    )


def _case_for(session: Session, run: EvalRun, case_key: str) -> EvalCaseRun:
    return session.scalars(
        select(EvalCaseRun).where(
            EvalCaseRun.eval_run_id == run.id, EvalCaseRun.case_key == case_key
        )
    ).one()


def _queue_shard(
    session: Session,
    run: EvalRun,
    *,
    now: datetime,
    deadline: datetime | None,
    queue_started_at: datetime | None,
) -> list[EvalCaseAttempt]:
    attempts = _attempt_rows(session, run)
    for attempt in attempts:
        attempt.status = "queued"
        attempt.phase_changed_at = now
        attempt.queue_started_at = queue_started_at
        attempt.queue_deadline = deadline
    session.commit()
    return attempts


class FakeTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url: str, *, method: str, body, headers, timeout: float):
        self.calls.append({"url": url})
        item = self.responses.pop(0) if self.responses else (204, "")
        return item


def test_dispatch_stamps_initial_progress_lease(
    session: Session, lease_settings, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = _make_run(session, key="lease-dispatch")
    now = datetime.now(UTC)

    outcome = dispatcher.dispatch_pending_outbox(
        session, transport=FakeTransport([(204, "")]), now=now
    )

    assert outcome.dispatched == 1
    attempts = _attempt_rows(session, run)
    assert len(attempts) == 3
    for attempt in attempts:
        assert attempt.status == "queued"
        assert _seconds(_aware(attempt.queue_started_at), now) == pytest.approx(0, abs=1)
        expected = now + timedelta(
            seconds=queue_lease.queue_budget_seconds(run, get_settings())
        )
        assert _seconds(_aware(attempt.queue_deadline), expected) == pytest.approx(0, abs=1)


def test_started_renews_waiting_siblings(
    session: Session, lease_settings, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = _make_run(session, key="lease-start-renew")
    now = datetime.now(UTC)
    _queue_shard(
        session,
        run,
        now=now,
        deadline=now + timedelta(seconds=5),
        queue_started_at=now - timedelta(seconds=100),
    )
    case_a = _case_for(session, run, "case-a")

    result = case_attempts.handle_started(
        session,
        payload={
            "case_run_id": case_a.id,
            "attempt": 1,
            "event_id": "start-a",
            "runner_id": "fixed-linux-eval",
        },
        now=now,
    )

    assert result["status"] == "running"
    # 60s lease from the start; the old 5s deadline must not survive.
    for attempt in _attempt_rows(session, run):
        if attempt.status == "running":
            continue
        assert _seconds(
            _aware(attempt.queue_deadline), now + timedelta(seconds=70)
        ) == pytest.approx(0, abs=1)

    # Ten seconds later the old 5s deadline would have killed them; the lease
    # keeps them queued while the started attempt is inside its own budget.
    assert reaper.sweep_lifecycle(session, settings=get_settings(), now=now + timedelta(seconds=10)) == {
        code: 0 for code in lifecycle.TIMEOUT_CODES
    }


def test_terminal_started_attempt_renews_waiting_siblings(
    session: Session, lease_settings, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = _make_run(session, key="lease-terminal-renew")
    now = datetime.now(UTC)
    _queue_shard(
        session,
        run,
        now=now,
        deadline=now + timedelta(seconds=5),
        queue_started_at=now - timedelta(seconds=100),
    )
    case_a = _case_for(session, run, "case-a")
    case_attempts.handle_started(
        session,
        payload={
            "case_run_id": case_a.id,
            "attempt": 1,
            "event_id": "start-a-terminal",
            "runner_id": "fixed-linux-eval",
        },
        now=now,
    )
    end = now + timedelta(seconds=31)
    case_attempts.handle_failed(
        session,
        payload={
            "case_run_id": case_a.id,
            "attempt": 1,
            "sequence": 2,
            "event_id": "fail-a-terminal",
            "error_code": "runtime_error",
            "error_summary": "合成失败",
            "retryable": False,
        },
        now=end,
    )

    assert _case_for(session, run, "case-a").execution_status == "failed"
    for attempt in _attempt_rows(session, run):
        if attempt.status in evaluations.TERMINAL_ATTEMPT_STATUSES:
            continue
        assert _seconds(
            _aware(attempt.queue_deadline), end + timedelta(seconds=70)
        ) == pytest.approx(0, abs=1)


def test_queue_timeout_does_not_renew_the_shard(
    session: Session, lease_settings, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = _make_run(session, key="lease-no-self-renew")
    now = datetime.now(UTC)
    attempts = _attempt_rows(session, run)
    attempts[0].status = "queued"
    attempts[0].phase_changed_at = now - timedelta(seconds=61)
    attempts[0].queue_started_at = now - timedelta(seconds=61)
    attempts[0].queue_deadline = now - timedelta(seconds=1)
    for sibling in attempts[1:]:
        sibling.status = "queued"
        sibling.phase_changed_at = now - timedelta(seconds=10)
        sibling.queue_started_at = now - timedelta(seconds=10)
        sibling.queue_deadline = now + timedelta(seconds=5)
    session.commit()

    counts = reaper.sweep_lifecycle(session, settings=get_settings(), now=now)

    assert counts[lifecycle.QUEUE_TIMEOUT] == 1
    timed_out = session.get(EvalCaseAttempt, attempts[0].id)
    assert timed_out.status == "timeout"
    # A queue timeout is not progress: the waiting siblings keep the old
    # deadline instead of receiving a fresh 60s lease.
    for sibling in _attempt_rows(session, run)[1:]:
        assert _seconds(
            _aware(sibling.queue_deadline), now + timedelta(seconds=5)
        ) == pytest.approx(0, abs=1)


def test_renewal_stops_at_the_absolute_ceiling(
    session: Session, lease_settings, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = _make_run(session, key="lease-ceiling")
    now = datetime.now(UTC)
    # The shard first entered the queue 295s ago; the 300s ceiling leaves only
    # five more seconds even though a fresh renewal budget is 60s.
    _queue_shard(
        session,
        run,
        now=now,
        deadline=now + timedelta(seconds=70),
        queue_started_at=now - timedelta(seconds=295),
    )
    case_a = _case_for(session, run, "case-a")
    case_attempts.handle_started(
        session,
        payload={
            "case_run_id": case_a.id,
            "attempt": 1,
            "event_id": "start-a-ceiling",
            "runner_id": "fixed-linux-eval",
        },
        now=now,
    )

    for attempt in _attempt_rows(session, run):
        if attempt.status == "running":
            continue
        assert _seconds(
            _aware(attempt.queue_deadline), now + timedelta(seconds=5)
        ) == pytest.approx(0, abs=1)

    # Six seconds later the capped deadline fires; the running attempt is still
    # inside its 60s execution budget, so only the two queue rows time out.
    counts = reaper.sweep_lifecycle(
        session, settings=get_settings(), now=now + timedelta(seconds=6)
    )
    assert counts[lifecycle.QUEUE_TIMEOUT] == 2
    assert counts[lifecycle.EXECUTION_TIMEOUT] == 0


def test_legacy_row_without_lease_falls_back_to_fixed_timeout(
    session: Session, lease_settings, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = _make_run(session, key="lease-legacy", case_keys=("case-a",))
    now = datetime.now(UTC)
    _queue_shard(
        session,
        run,
        now=now,
        deadline=None,
        queue_started_at=None,
    )
    attempt = _attempt_rows(session, run)[0]
    attempt.phase_changed_at = now - timedelta(seconds=61)
    session.commit()

    counts = reaper.sweep_lifecycle(session, settings=get_settings(), now=now)

    assert counts[lifecycle.QUEUE_TIMEOUT] == 1
    attempt = _attempt_rows(session, run)[0]
    assert attempt.status == "timeout"
    assert attempt.error_code == lifecycle.QUEUE_TIMEOUT
    event = session.scalars(
        select(EvalEvent).where(
            EvalEvent.eval_case_run_id == attempt.eval_case_run_id,
            EvalEvent.event_type == "case.timeout",
        )
    ).one()
    payload = event.payload_json
    assert payload["timeout_class"] == lifecycle.QUEUE_TIMEOUT
    assert payload["queue_wait_seconds"] is not None
    assert payload["queue_wait_seconds"] >= 60
    assert payload["queue_deadline"] is None
    assert payload["shard_size"] == 64
    assert payload["concurrency"] >= 1
    assert "queue_started_at" in payload
    assert "last_progress_at" in payload

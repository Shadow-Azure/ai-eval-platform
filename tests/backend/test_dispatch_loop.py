"""Dispatch worker: outbox → repository_dispatch → reconciliation.

验收对照：
- 派发失败（HTTP 错误 / 无 token）按退避重试，超过上限进入可诊断失败态
  （attempt dispatch_failed、case failed、run failed），绝不永久 queued。
- Worker 重启（processing 行 lease 过期）恢复重派，不产生重复 attempt。
- run 取消后不再派发。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox
from app.seed import ensure_default_scope
from app.services import dispatcher, evaluations
from sqlalchemy.orm import Session


def aware(value):
    """SQLite hands back naive UTC datetimes; normalize for comparisons."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


class FakeTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url: str, *, method: str, body, headers, timeout: float):
        import json

        self.calls.append({"url": url, "body": json.loads(body.decode())})
        item = self.responses.pop(0) if self.responses else (204, "")
        if isinstance(item, Exception):
            raise item
        return item


def create_run(session: Session, *, case_keys: list[str] | None = None, key: str = "k"):
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="派发冒烟",
        case_keys=case_keys or ["a", "b"],
        idempotency_key=key,
        created_by="agent",
        settings=get_settings(),
    )
    return run


def outbox_rows(session: Session) -> list[Outbox]:
    return list(session.query(Outbox).order_by(Outbox.id).all())


def test_dispatch_success_marks_outbox_sent_and_attempts_queued(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = create_run(seeded, key="ok1")
    transport = FakeTransport([(204, "")])

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)

    assert outcome.dispatched == 1  # 2 cases / default shard_size 64 → 1 shard
    assert len(transport.calls) == 1
    for row in outbox_rows(seeded):
        assert row.status == "sent"
        assert row.payload_json["dispatched_at"]
        assert row.payload_json["github_status"] == 204
    attempts = seeded.query(EvalCaseAttempt).all()
    assert {a.status for a in attempts} == {"queued"}
    assert {a.dispatch_status for a in attempts} == {"sent"}
    run_row = seeded.query(EvalRun).filter(EvalRun.id == run.id).one()
    assert run_row.status == "queued"  # not started yet

    events = (
        seeded.query(EvalEvent)
        .filter(EvalEvent.eval_run_id == run.id, EvalEvent.event_type == "eval.dispatched")
        .all()
    )
    assert len(events) == 1

    # Re-running the loop dispatches nothing new (worker restart safety).
    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=FakeTransport([]))
    assert outcome.dispatched == 0


def test_dispatch_http_failure_backs_off_then_retries(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    create_run(seeded, key="f1")
    transport = FakeTransport([(403, '{"message":"not allowed"}')])

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)
    assert outcome.dispatched == 0
    row = outbox_rows(seeded)[0]
    assert row.status == "pending"
    assert row.attempts == 1
    assert "not allowed" in row.payload_json["last_error"]
    assert aware(row.available_at) > datetime.now(UTC)

    attempt_rows = seeded.query(EvalCaseAttempt).all()
    assert {a.status for a in attempt_rows} == {"pending"}  # nothing lost, nothing double

    # Lease expiry passes → retry succeeds.
    seeded.expire_all()
    row.available_at = datetime.now(UTC)
    seeded.commit()
    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=FakeTransport([(204, "")]))
    assert outcome.dispatched == 1
    assert outbox_rows(seeded)[0].status == "sent"
    assert seeded.query(EvalCaseAttempt).count() == 2


def test_dispatch_failure_exhaustion_reaches_diagnosable_failed_state(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token", DISPATCH_MAX_ATTEMPTS="2")
    run = create_run(seeded, key="fail-all")

    for _ in range(3):  # more loops than max attempts
        seeded.expire_all()
        for row in outbox_rows(seeded):
            row.available_at = datetime.now(UTC)
        seeded.commit()
        dispatcher.dispatch_pending_outbox(seeded, transport=FakeTransport([(500, "boom")]))

    for row in outbox_rows(seeded):
        assert row.status == "failed"
        assert row.attempts == 2
        assert row.payload_json["last_error"]

    attempts = seeded.query(EvalCaseAttempt).all()
    assert {a.status for a in attempts} == {"dispatch_failed"}
    cases = seeded.query(EvalCaseRun).all()
    assert {c.execution_status for c in cases} == {"failed"}
    case = cases[0]
    assert case.execution_status == "failed"
    assert case.error_code == "dispatch_failed"
    run_row = seeded.query(EvalRun).filter(EvalRun.id == run.id).one()
    assert run_row.status == "failed"
    assert run_row.finished_at is not None

    events = {
        e.event_type
        for e in seeded.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).all()
    }
    assert "eval.dispatch_failed" in events

    # Terminal: no more GitHub calls, no state change.
    transport = FakeTransport([])
    dispatcher.dispatch_pending_outbox(seeded, transport=transport)
    assert transport.calls == []


def test_missing_github_token_fails_diagnosably(seeded: Session, configure_settings) -> None:
    configure_settings(GITHUB_TOKEN="", DISPATCH_MAX_ATTEMPTS="1")
    run = create_run(seeded, key="no-token")

    dispatcher.dispatch_pending_outbox(seeded, transport=FakeTransport([]))
    row = outbox_rows(seeded)[0]
    assert row.status == "failed"
    assert "token" in row.payload_json["last_error"].lower()
    assert seeded.query(EvalRun).filter(EvalRun.id == run.id).one().status == "failed"


def test_stuck_processing_lease_is_recovered_without_duplicate_attempts(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token", DISPATCH_LEASE_SECONDS="60")
    create_run(seeded, key="lease1")
    row = outbox_rows(seeded)[0]
    # Simulate a worker crash mid-dispatch: row claimed but never finished.
    row.status = "processing"
    row.updated_at = datetime.now(UTC) - timedelta(seconds=120)
    seeded.commit()

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=FakeTransport([(204, "")]))
    assert outcome.dispatched == 1
    assert outbox_rows(seeded)[0].status == "sent"
    # The crash recovery must not have duplicated the attempt rows.
    assert seeded.query(EvalCaseAttempt).count() == 2
    # The recovered dispatch is fresh: the shard went out exactly once.
    assert row.attempts == 1


def test_cancelled_run_never_dispatches(seeded: Session, configure_settings) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = create_run(seeded, key="cancel1")
    evaluations.cancel_evaluation(seeded, run_id=run.id)
    transport = FakeTransport([])

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)
    assert outcome.dispatched == 0
    assert transport.calls == []
    assert all(row.status == "cancelled" for row in outbox_rows(seeded))


def test_only_due_rows_are_dispatched(seeded: Session, configure_settings) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    create_run(seeded, key="due1")
    row = outbox_rows(seeded)[0]
    row.available_at = datetime.now(UTC) + timedelta(minutes=5)
    seeded.commit()

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=FakeTransport([]))
    assert outcome.dispatched == 0
    assert outbox_rows(seeded)[0].status == "pending"

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

        payload = json.loads(body.decode())
        self.calls.append({"url": url, "body": payload})
        # M8-51 (#323): reproduce GitHub's dispatch parameter validation so a
        # >10-property payload can never slip through the tests unnoticed.
        if len(payload.get("client_payload") or {}) > 10:
            return (
                422,
                '{"message":"No more than 10 properties are allowed; '
                f'{len(payload["client_payload"])} were supplied."}}',
            )
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


def test_dispatch_pins_the_scripts_bundle_for_image_cache_lookup(
    seeded: Session, configure_settings
) -> None:
    name = "eval-runner-scripts-0123456789abcdef0123456789abcdef.tar.gz"
    configure_settings(
        GITHUB_TOKEN="gh-token",
        RUNNER_SCRIPTS_BUNDLE_NAME=name,
        RUNNER_SCRIPTS_BUNDLE_MD5="0" * 32,
        RUNNER_SCRIPTS_BUNDLE_SHA256="a" * 64,
        RUNNER_SCRIPTS_BUNDLE_SIZE="40960",
    )
    create_run(seeded, key="bundle-pin")
    transport = FakeTransport([(204, "")])

    dispatcher.dispatch_pending_outbox(seeded, transport=transport)

    assert len(transport.calls) == 1
    payload = transport.calls[0]["body"]["client_payload"]
    # M8-51 (#323) option C: the pin stays in the outbox row and is fetched
    # through the `/scripts` metadata endpoint; the dispatch keeps only the
    # eight pre-start fields.
    assert "scripts_bundle" not in payload
    assert payload["schema_version"] == "1.1"
    assert len(payload) == 8
    assert "manifest_digest" not in payload
    assert "expires_at" not in payload
    row = outbox_rows(seeded)[0]
    assert row.payload_json["scripts_bundle"] == {
        "name": name,
        "md5": "0" * 32,
        "sha256": "a" * 64,
        "size": 40960,
    }
    assert row.payload_json["manifest_digest"].startswith("sha256:")
    assert row.payload_json["expires_at"]


def test_dispatch_omits_an_incomplete_scripts_bundle_pin(
    seeded: Session, configure_settings
) -> None:
    configure_settings(
        GITHUB_TOKEN="gh-token",
        RUNNER_SCRIPTS_BUNDLE_NAME="eval-runner-scripts-0123456789abcdef0123456789abcdef.tar.gz",
    )
    create_run(seeded, key="bundle-incomplete")
    transport = FakeTransport([(204, "")])

    dispatcher.dispatch_pending_outbox(seeded, transport=transport)

    assert "scripts_bundle" not in transport.calls[0]["body"]["client_payload"]


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


# ---------------------------------------------------------------------------
# M8-51 (#323): transport preflight and 422 classification
# ---------------------------------------------------------------------------


def _mutate_client_payload(session: Session, mutate) -> None:
    row = outbox_rows(session)[0]
    client_payload = dict(row.payload_json["client_payload"])
    mutate(client_payload)
    row.payload_json = {**row.payload_json, "client_payload": client_payload}
    session.commit()


def test_dispatch_guard_rejects_eleven_properties_without_calling_transport(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = create_run(seeded, key="guard-11")
    # The minimal 1.1 payload has 8 keys; three injected keys reach GitHub's
    # 10-property ceiling.
    _mutate_client_payload(
        seeded,
        lambda payload: payload.update({"extra_a": "boom", "extra_b": "boom", "extra_c": "boom"}),
    )
    transport = FakeTransport([])

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)

    assert transport.calls == []
    assert outcome.failed == 1
    row = outbox_rows(seeded)[0]
    assert row.status == "failed"
    assert row.attempts == 1  # terminal: the retry budget is not consumed
    assert "dispatch_payload_invalid" in row.payload_json["last_error"]
    assert "11" in row.payload_json["last_error"]
    assert {a.status for a in seeded.query(EvalCaseAttempt).all()} == {"dispatch_failed"}
    assert seeded.query(EvalRun).filter(EvalRun.id == run.id).one().status == "failed"
    events = {
        e.event_type
        for e in seeded.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).all()
    }
    assert "eval.dispatch_failed" in events


def test_dispatch_guard_rejects_oversized_payload_without_calling_transport(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    create_run(seeded, key="guard-64k")
    # Keep the 8 top-level keys and grow one value past the local byte guard.
    _mutate_client_payload(
        seeded, lambda payload: payload.update({"manifest_url": "http://x/" + "a" * (61 * 1024)})
    )
    transport = FakeTransport([])

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)

    assert transport.calls == []
    assert outcome.failed == 1
    row = outbox_rows(seeded)[0]
    assert row.status == "failed"
    assert "dispatch_payload_invalid" in row.payload_json["last_error"]
    assert "字节" in row.payload_json["last_error"]


def test_dispatch_guard_rejects_overlong_event_type(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token", GITHUB_DISPATCH_EVENT_TYPE="e" * 101)
    create_run(seeded, key="guard-event")
    transport = FakeTransport([])

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)

    assert transport.calls == []
    assert outcome.failed == 1
    row = outbox_rows(seeded)[0]
    assert row.status == "failed"
    assert "dispatch_payload_invalid" in row.payload_json["last_error"]
    assert "101" in row.payload_json["last_error"]


def test_422_parameter_validation_is_terminal(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token", DISPATCH_MAX_ATTEMPTS="5")
    run = create_run(seeded, key="422-terminal")
    transport = FakeTransport(
        [(422, '{"message":"No more than 10 properties are allowed; 11 were supplied."}')]
    )

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)

    assert len(transport.calls) == 1
    assert outcome.retried == 0
    assert outcome.failed == 1
    row = outbox_rows(seeded)[0]
    assert row.status == "failed"
    assert row.attempts == 1
    assert "github_dispatch_failed:422" in row.payload_json["last_error"]
    assert "No more than 10 properties" in row.payload_json["last_error"]
    assert {a.status for a in seeded.query(EvalCaseAttempt).all()} == {"dispatch_failed"}
    assert seeded.query(EvalRun).filter(EvalRun.id == run.id).one().status == "failed"


def test_422_spammed_endpoint_is_retried(seeded: Session, configure_settings) -> None:
    configure_settings(GITHUB_TOKEN="gh-token", DISPATCH_MAX_ATTEMPTS="5")
    create_run(seeded, key="422-spam")
    transport = FakeTransport([(422, '{"message":"The endpoint has been spammed."}')])

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)

    assert len(transport.calls) == 1
    assert outcome.retried == 1
    row = outbox_rows(seeded)[0]
    assert row.status == "pending"
    assert row.attempts == 1
    assert "spammed" in row.payload_json["last_error"]
    assert aware(row.available_at) > datetime.now(UTC)


def test_429_rate_limit_is_retried(seeded: Session, configure_settings) -> None:
    configure_settings(GITHUB_TOKEN="gh-token", DISPATCH_MAX_ATTEMPTS="5")
    create_run(seeded, key="429-retry")
    transport = FakeTransport([(429, '{"message":"API rate limit exceeded"}')])

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)

    assert outcome.retried == 1
    row = outbox_rows(seeded)[0]
    assert row.status == "pending"
    assert row.attempts == 1


"""M4-01 case-level matrix scheduling: effective concurrency, dispatcher gate,
and the started-callback hard cap.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox, ResourcePool, Runner
from app.seed import ensure_default_scope
from app.services import capacity, case_attempts, dispatcher, evaluations
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


class FakeTransport:
    def __init__(self, responses=None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def __call__(self, url: str, *, method: str, body, headers, timeout: float):
        self.calls.append({"url": url, "body": json.loads(body.decode())})
        item = self.responses.pop(0) if self.responses else (204, "")
        if isinstance(item, Exception):
            raise item
        return item


def _make_pool(
    session: Session,
    *,
    runners: int = 0,
    labels: dict | None = None,
    status: str = "active",
    heartbeat_at: datetime | None = None,
    key: str = "pool",
) -> ResourcePool:
    tenant, _workspace, project = ensure_default_scope(session)
    pool = ResourcePool(
        tenant_id=tenant.id,
        project_id=project.id,
        name=f"pool-{key}",
        provider_key="fixed",
        labels_json=labels if labels is not None else {"kind": "fixed"},
        status=status,
    )
    session.add(pool)
    session.flush()
    for index in range(runners):
        session.add(
            Runner(
                tenant_id=tenant.id,
                resource_pool_id=pool.id,
                provider_runner_id=f"runner-{key}-{index}",
                name=f"runner-{key}-{index}",
                labels_json=["self-hosted", "linux", "eval"],
                status="online",
                last_heartbeat_at=heartbeat_at,
            )
        )
    session.commit()
    return pool


def _create_run(
    session: Session,
    *,
    pool: ResourcePool | None = None,
    case_count: int = 2,
    shard_size: int = 64,
    requested: int = 8,
    key: str = "run",
) -> EvalRun:
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="M4-01 容量",
        case_keys=[f"case-{index}" for index in range(case_count)],
        shard_size=shard_size,
        requested_concurrency=requested,
        resource_pool_id=pool.id if pool is not None else None,
        idempotency_key=key,
        created_by="agent",
        settings=get_settings(),
    )
    return run


# ---------------------------------------------------------------------------
# Effective concurrency
# ---------------------------------------------------------------------------


def test_effective_concurrency_is_min_of_inputs(seeded: Session, configure_settings) -> None:
    configure_settings(RUNNER_HEARTBEAT_TIMEOUT_SECONDS="90")
    pool = _make_pool(seeded, runners=3, labels={"max_capacity": 5}, key="min")
    run = _create_run(seeded, pool=pool, requested=8, key="min")
    assert capacity.effective_concurrency(seeded, run, settings=get_settings()) == 3

    unlimited = _make_pool(seeded, runners=3, labels={}, key="unlimited")
    unlimited_run = _create_run(seeded, pool=unlimited, requested=8, key="unlimited")
    assert capacity.effective_concurrency(seeded, unlimited_run, settings=get_settings()) == 3

    empty = _make_pool(seeded, runners=0, key="empty")
    empty_run = _create_run(seeded, pool=empty, requested=8, key="empty")
    assert capacity.effective_concurrency(seeded, empty_run, settings=get_settings()) == 0

    ungated = _create_run(seeded, pool=None, requested=8, key="ungated")
    assert capacity.effective_concurrency(seeded, ungated, settings=get_settings()) == 8


def test_stale_heartbeat_runner_is_not_counted(seeded: Session, configure_settings) -> None:
    configure_settings(RUNNER_HEARTBEAT_TIMEOUT_SECONDS="90")
    pool = _make_pool(seeded, runners=0, key="stale")
    tenant, _workspace, project = ensure_default_scope(seeded)
    now = datetime.now(UTC)
    seeded.add_all(
        [
            Runner(
                tenant_id=tenant.id,
                resource_pool_id=pool.id,
                provider_runner_id="fresh",
                name="fresh",
                labels_json=["self-hosted"],
                status="online",
                last_heartbeat_at=now,
            ),
            Runner(
                tenant_id=tenant.id,
                resource_pool_id=pool.id,
                provider_runner_id="stale",
                name="stale",
                labels_json=["self-hosted"],
                status="online",
                last_heartbeat_at=now - timedelta(seconds=120),
            ),
        ]
    )
    seeded.commit()
    run = _create_run(seeded, pool=pool, requested=4, key="stale")
    assert capacity.effective_concurrency(seeded, run, settings=get_settings()) == 1


def test_500_cases_form_8_shards_of_64(seeded: Session) -> None:
    run = _create_run(seeded, case_count=500, shard_size=64, requested=8, key="shards")

    rows = list(
        seeded.scalars(
            select(Outbox).where(Outbox.aggregate_id == run.id).order_by(Outbox.id)
        )
    )
    assert [row.payload_json["shard_id"] for row in rows] == [
        f"shard_{index:03d}" for index in range(8)
    ]
    case_counts = [
        len(json.loads(row.payload_json["manifest_text"])["cases"]) for row in rows
    ]
    assert case_counts == [64] * 7 + [52]
    assert all(count <= 64 for count in case_counts)


# ---------------------------------------------------------------------------
# Dispatcher capacity gate
# ---------------------------------------------------------------------------


def test_gate_holds_shards_when_no_runner(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    pool = _make_pool(seeded, runners=0, key="hold")
    run = _create_run(seeded, pool=pool, case_count=500, requested=8, key="hold")

    outcome = dispatcher.dispatch_pending_outbox(
        seeded, settings=get_settings(), transport=FakeTransport()
    )

    assert outcome.dispatched == 0
    assert outcome.queued == 8
    assert {row.status for row in seeded.query(Outbox).all()} == {"pending"}
    assert {attempt.status for attempt in seeded.query(EvalCaseAttempt).all()} == {"pending"}
    refreshed = seeded.get(EvalRun, run.id)
    assert refreshed.queue_reason == "capacity_waiting"
    assert refreshed.effective_concurrency == 0


def test_gate_releases_one_shard_at_a_time(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    pool = _make_pool(seeded, runners=8, key="release")
    run = _create_run(seeded, pool=pool, case_count=500, requested=8, key="release")
    transport = FakeTransport()

    first = dispatcher.dispatch_pending_outbox(
        seeded, settings=get_settings(), transport=transport
    )
    assert first.dispatched == 1
    assert first.queued == 7
    assert seeded.get(EvalRun, run.id).effective_concurrency == 8

    # Deferred rows sit at now + retry seconds; advance the clock instead of sleeping.
    later = datetime.now(UTC) + timedelta(seconds=30)
    second = dispatcher.dispatch_pending_outbox(
        seeded, settings=get_settings(), transport=transport, now=later
    )
    assert second.dispatched == 0, "an in-flight shard already consumes the budget"
    assert second.queued == 7

    # Finish the first shard: its attempts go terminal, so the next shard may go.
    shard = seeded.query(EvalCaseAttempt).filter(EvalCaseAttempt.shard_id == "shard_000").all()
    for attempt in shard:
        attempt.status = "succeeded"
    seeded.commit()

    third = dispatcher.dispatch_pending_outbox(
        seeded, settings=get_settings(), transport=transport, now=later + timedelta(seconds=30)
    )
    assert third.dispatched == 1
    assert {
        call["body"]["client_payload"]["shard_id"] for call in transport.calls
    } == {"shard_000", "shard_001"}


def test_queued_work_does_not_duplicate_attempts(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    pool = _make_pool(seeded, runners=0, key="dup")
    _create_run(seeded, pool=pool, case_count=500, requested=8, key="dup")

    for _ in range(3):
        dispatcher.dispatch_pending_outbox(
            seeded, settings=get_settings(), transport=FakeTransport()
        )

    assert seeded.query(EvalCaseAttempt).count() == 500
    assert seeded.query(Outbox).count() == 8
    assert {row.status for row in seeded.query(Outbox).all()} == {"pending"}


def test_gate_off_is_passthrough(seeded: Session, configure_settings) -> None:
    configure_settings(GITHUB_TOKEN="gh-token", DISPATCH_CAPACITY_GATE="false")
    pool = _make_pool(seeded, runners=0, key="off")
    _create_run(seeded, pool=pool, case_count=130, requested=8, key="off")

    outcome = dispatcher.dispatch_pending_outbox(
        seeded, settings=get_settings(), transport=FakeTransport()
    )
    assert outcome.dispatched == 3
    assert outcome.queued == 0


def test_run_without_pool_is_never_gated(seeded: Session, configure_settings) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    _create_run(seeded, pool=None, case_count=130, requested=8, key="nopool")

    outcome = dispatcher.dispatch_pending_outbox(
        seeded, settings=get_settings(), transport=FakeTransport()
    )
    assert outcome.dispatched == 3


# ---------------------------------------------------------------------------
# Started hard cap
# ---------------------------------------------------------------------------


def _started_payload(case_run_id: str, event_id: str) -> dict:
    return {
        "case_run_id": case_run_id,
        "attempt": 1,
        "event_id": event_id,
    }


def test_started_rejects_over_cap_and_succeeds_after_capacity_frees(
    seeded: Session, configure_settings
) -> None:
    configure_settings(RUNNER_HEARTBEAT_TIMEOUT_SECONDS="90")
    pool = _make_pool(seeded, runners=1, key="started")
    run = _create_run(seeded, pool=pool, case_count=2, requested=1, key="started")
    cases = list(
        seeded.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )

    case_attempts.handle_started(
        seeded, payload=_started_payload(cases[0].id, "evt-a-start")
    )
    assert seeded.get(EvalCaseRun, cases[0].id).execution_status == "running"

    with pytest.raises(case_attempts.CaseCallbackError) as rejected:
        case_attempts.handle_started(
            seeded, payload=_started_payload(cases[1].id, "evt-b-start")
        )
    assert rejected.value.status_code == 429
    assert rejected.value.code == "capacity_waiting"
    assert seeded.get(EvalCaseRun, cases[1].id).execution_status == "queued"
    assert seeded.get(EvalRun, run.id).queue_reason == "concurrency_cap"
    events = (
        seeded.query(EvalEvent)
        .filter(EvalEvent.eval_run_id == run.id, EvalEvent.event_type == "case.started")
        .all()
    )
    assert len(events) == 1

    # Finish the running case; the queued case then starts exactly once.
    case_a = seeded.get(EvalCaseRun, cases[0].id)
    case_a.execution_status = "succeeded"
    attempt_a = seeded.query(EvalCaseAttempt).filter(EvalCaseAttempt.eval_case_run_id == cases[0].id).one()
    attempt_a.status = "succeeded"
    seeded.commit()

    result = case_attempts.handle_started(
        seeded, payload=_started_payload(cases[1].id, "evt-b-start")
    )
    assert result["deduped"] is False
    assert seeded.get(EvalCaseRun, cases[1].id).execution_status == "running"
    assert seeded.get(EvalRun, run.id).queue_reason is None

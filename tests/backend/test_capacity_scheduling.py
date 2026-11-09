"""M4-01 case-level matrix scheduling: effective concurrency, dispatcher gate,
and the started-callback hard cap.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
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


def _started_payload(case_run_id: str, event_id: str, runner_id: str | None = None) -> dict:
    payload = {
        "case_run_id": case_run_id,
        "attempt": 1,
        "event_id": event_id,
    }
    if runner_id:
        payload["runner_id"] = runner_id
    return payload


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


def test_two_runners_reach_cap_and_hold_excess(
    seeded: Session, configure_settings
) -> None:
    """M4-01 multi-runner evidence: two runners fill the cap, the rest wait."""

    configure_settings(RUNNER_HEARTBEAT_TIMEOUT_SECONDS="90")
    pool = _make_pool(seeded, runners=2, key="two")
    run = _create_run(seeded, pool=pool, case_count=4, requested=2, key="two")
    cases = list(
        seeded.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )

    for case, runner in ((cases[0], "runner-two-0"), (cases[1], "runner-two-1")):
        case_attempts.handle_started(
            seeded,
            payload=_started_payload(case.id, f"evt-{case.id}", runner_id=runner),
        )

    attempts = {
        attempt.eval_case_run_id: attempt
        for attempt in seeded.query(EvalCaseAttempt).all()
    }
    assert {attempts[case.id].runner_id for case in cases[:2]} != {None}
    assert len({attempts[case.id].runner_id for case in cases[:2]}) == 2
    assert seeded.get(EvalRun, run.id).effective_concurrency == 2

    for case in cases[2:]:
        with pytest.raises(case_attempts.CaseCallbackError) as rejected:
            case_attempts.handle_started(
                seeded,
                payload=_started_payload(case.id, f"evt-{case.id}", runner_id="runner-two-0"),
            )
        assert rejected.value.status_code == 429
        assert seeded.get(EvalCaseRun, case.id).execution_status == "queued"

    # One runner frees up → exactly one queued case may start.
    completed = seeded.get(EvalCaseRun, cases[0].id)
    completed.execution_status = "succeeded"
    attempts[cases[0].id].status = "succeeded"
    seeded.commit()
    case_attempts.handle_started(
        seeded, payload=_started_payload(cases[2].id, "evt-late", runner_id="runner-two-0")
    )
    assert seeded.get(EvalCaseRun, cases[2].id).execution_status == "running"
    assert seeded.get(EvalCaseRun, cases[3].id).execution_status == "queued"


def test_stuck_lease_requeues_dispatching_attempts(
    seeded: Session, configure_settings
) -> None:
    """A dispatcher crash must not park the shard forever as 'in flight'."""

    configure_settings(GITHUB_TOKEN="gh-token")
    pool = _make_pool(seeded, runners=8, key="lease")
    _create_run(seeded, pool=pool, case_count=2, requested=8, key="lease")
    row = seeded.query(Outbox).one()
    row.status = "processing"
    row.updated_at = datetime.now(UTC) - timedelta(seconds=9999)
    for attempt in seeded.query(EvalCaseAttempt).all():
        attempt.status = "dispatching"
        attempt.dispatch_status = "dispatching"
    seeded.commit()

    outcome = dispatcher.dispatch_pending_outbox(
        seeded, settings=get_settings(), transport=FakeTransport()
    )

    assert outcome.recovered == 1
    assert outcome.dispatched == 1
    seeded.expire_all()
    assert {attempt.status for attempt in seeded.query(EvalCaseAttempt).all()} == {"queued"}


def test_duplicate_started_from_another_runner_is_stale(
    seeded: Session, configure_settings
) -> None:
    configure_settings(RUNNER_HEARTBEAT_TIMEOUT_SECONDS="90")
    pool = _make_pool(seeded, runners=2, key="dupstart")
    run = _create_run(seeded, pool=pool, case_count=1, requested=1, key="dupstart")
    case = seeded.scalars(
        select(EvalCaseRun).where(EvalCaseRun.eval_run_id == run.id)
    ).one()
    payload = _started_payload(case.id, "evt-dup", runner_id="runner-dupstart-0")
    case_attempts.handle_started(seeded, payload=payload)

    with pytest.raises(case_attempts.StaleAttempt):
        case_attempts.handle_started(
            seeded, payload={**payload, "runner_id": "runner-dupstart-1"}
        )

    assert seeded.get(EvalCaseRun, case.id).execution_status == "running"


def test_late_shard_gets_a_fresh_dispatch_window(
    seeded: Session, configure_settings
) -> None:
    """Creation-time deadlines must not expire before a gated shard is sent."""

    configure_settings(GITHUB_TOKEN="gh-token")
    pool = _make_pool(seeded, runners=8, key="window")
    _create_run(seeded, pool=pool, case_count=500, requested=8, key="window")
    transport = FakeTransport()
    dispatcher.dispatch_pending_outbox(
        seeded, settings=get_settings(), transport=transport
    )
    first = seeded.query(Outbox).filter(Outbox.status == "sent").one()
    first_id = first.id
    first_shard = first.payload_json["shard_id"]
    for attempt in (
        seeded.query(EvalCaseAttempt).filter(EvalCaseAttempt.shard_id == first_shard).all()
    ):
        attempt.status = "succeeded"
    seeded.commit()

    later = datetime.now(UTC) + timedelta(hours=2)
    dispatcher.dispatch_pending_outbox(
        seeded, settings=get_settings(), transport=transport, now=later
    )
    sent = [row for row in seeded.query(Outbox).all() if row.status == "sent"]
    second = next(row for row in sent if row.id != first_id)

    text = second.payload_json["manifest_text"]
    manifest = json.loads(text)
    deadline = datetime.fromisoformat(manifest["deadline"].replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(
        second.payload_json["client_payload"]["expires_at"].replace("Z", "+00:00")
    )
    assert deadline > later + timedelta(hours=1)
    assert expires_at > later
    assert second.payload_json["manifest_digest"] == (
        "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    )
    assert second.payload_json["client_payload"]["manifest_digest"] == (
        second.payload_json["manifest_digest"]
    )


def test_started_gate_is_atomic_under_concurrency(engine, configure_settings) -> None:
    """Two concurrent started callbacks must never exceed the cap (regression)."""

    configure_settings(RUNNER_HEARTBEAT_TIMEOUT_SECONDS="90")
    with Session(engine) as setup:
        ensure_default_scope(setup)
        pool = _make_pool(setup, runners=1, key="atomic")
        run = _create_run(setup, pool=pool, case_count=2, requested=1, key="atomic")
        run_id = run.id
        case_ids = [
            case.id
            for case in setup.scalars(
                select(EvalCaseRun)
                .where(EvalCaseRun.eval_run_id == run_id)
                .order_by(EvalCaseRun.ordinal)
            )
        ]

    barrier = threading.Barrier(2)

    def start(case_id: str, event_id: str) -> str | int:
        with Session(engine) as thread_session:
            barrier.wait(timeout=10)
            try:
                case_attempts.handle_started(
                    thread_session,
                    payload=_started_payload(
                        case_id, event_id, runner_id="runner-atomic-0"
                    ),
                )
                return "ok"
            except case_attempts.CaseCallbackError as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result()
            for future in (
                executor.submit(start, case_ids[0], "evt-atomic-0"),
                executor.submit(start, case_ids[1], "evt-atomic-1"),
            )
        ]

    assert results.count("ok") == 1
    assert 429 in results
    with Session(engine) as check:
        statuses = [
            case.execution_status
            for case in check.scalars(
                select(EvalCaseRun).where(EvalCaseRun.eval_run_id == run_id)
            )
        ]
    assert statuses.count("running") == 1


def test_capacity_rejection_releases_runner_binding(
    seeded: Session, client, configure_settings
) -> None:
    """A 429 started must not leave the runner bound to a case it never ran."""

    configure_settings(RUNNER_TASK_SECRET="", RUNNER_HEARTBEAT_TIMEOUT_SECONDS="90")
    pool = _make_pool(seeded, runners=2, key="bind")
    run = _create_run(seeded, pool=pool, case_count=2, requested=1, key="bind")
    cases = list(
        seeded.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )

    def start(case_id: str, runner_id: str, event_id: str):
        return client.post(
            f"/runner/v1/case-runs/{case_id}/started",
            json={
                "schema_version": "1.0",
                "event_id": event_id,
                "case_run_id": case_id,
                "attempt": 1,
                "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            headers={"x-runner-id": runner_id},
        )

    first = start(cases[0].id, "runner-bind-0", "evt-bind-a")
    assert first.status_code == 200, first.text

    second = start(cases[1].id, "runner-bind-1", "evt-bind-b")
    assert second.status_code == 429, second.text
    assert second.json()["detail"]["error"]["code"] == "capacity_waiting"

    seeded.expire_all()
    runner = (
        seeded.query(Runner)
        .filter(Runner.provider_runner_id == "runner-bind-1")
        .one()
    )
    assert runner.busy_case_attempt_id is None
    assert runner.status == "online"

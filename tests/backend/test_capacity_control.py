"""M4-03 B: capacity controller for spot resource pools.

Covers the acceptance surface of issue #80 that part A does not:

- the pool capacity state (queued/idle/starting/gap/desired) and the persisted
  price/capacity policy snapshot;
- scale-up batch creation through the provisioner seam, including the price
  threshold, inventory shortage and provider failure. None of these may fail
  evaluation cases: they only keep shards queued with a stable reason;
- scale-down ordering: a runner is drained and deregistered *before* its
  instance is released, and a release failure keeps the instance and continues;
- event dedupe so repeated worker ticks do not spam the resource event log;
- fixed pools are never reconciled by the worker sweep;
- the worker loop runs one capacity pass per tick and logs it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.config import get_settings
from app.models import (
    Connection,
    Credential,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    ProvisionedInstance,
    ResourceEvent,
    ResourcePool,
    Runner,
)
from app.seed import ensure_default_scope
from app.services import capacity_control
from app.services.provisioning import reset_provisioners
from app.services.provisioning.fake import FakeProvisioner, reset_fake_instance_counter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_provisioning() -> object:
    reset_provisioners()
    reset_fake_instance_counter()
    yield
    reset_provisioners()
    reset_fake_instance_counter()


@pytest.fixture
def scope(session: Session) -> dict[str, str]:
    tenant, _workspace, project = ensure_default_scope(session)
    return {"tenant_id": tenant.id, "project_id": project.id}


def _credential(
    session: Session,
    scope: dict[str, str],
    *,
    provider: str = "fake",
    kind: str = "resource",
    revoked: bool = False,
) -> Credential:
    credential = Credential(
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
        kind=kind,
        provider=provider,
        ciphertext="ciphertext-must-never-leak",
        key_version="local:test-key",
        secret_mask="ak-***mask",
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    session.add(credential)
    session.flush()
    return credential


def _connection(
    session: Session,
    scope: dict[str, str],
    *,
    provider: str = "fake",
    capabilities: tuple[str, ...] = ("ecs:spot", "ecs:inventory"),
    region: str | None = "cn-hangzhou",
) -> Connection:
    connection = Connection(
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
        name=f"conn-{uuid4().hex[:8]}",
        provider=provider,
        region=region,
        credential_id=_credential(session, scope, provider=provider).id,
        capabilities_json={"capabilities": list(capabilities)},
        status="verified",
    )
    session.add(connection)
    session.flush()
    return connection


def _pool(session: Session, scope: dict[str, str], **overrides: object) -> ResourcePool:
    values: dict[str, object] = {
        "tenant_id": scope["tenant_id"],
        "project_id": scope["project_id"],
        "name": f"pool-{uuid4().hex[:8]}",
        "provider_key": "fake",
        "provisioning_mode": "spot",
        "min_capacity": 0,
        "max_capacity": 5,
        "instance_config_json": {
            "instance_type": "ecs.g7.large",
            "runners_per_instance": 1,
        },
        "labels_json": {"kind": "spot"},
        "status": "active",
    }
    values.update(overrides)
    pool = ResourcePool(**values)
    session.add(pool)
    session.flush()
    return pool


def _spot_pool_with_connection(
    session: Session, scope: dict[str, str], *, provider: str = "fake", **overrides: object
) -> ResourcePool:
    connection = _connection(session, scope, provider=provider)
    return _pool(
        session, scope, connection_id=connection.id, provider_key=provider, **overrides
    )


def _queued_cases(
    session: Session,
    scope: dict[str, str],
    pool: ResourcePool,
    count: int,
    *,
    run_status: str = "queued",
) -> EvalRun:
    """One run bound to ``pool`` with ``count`` attempts waiting in queue."""

    run = EvalRun(
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
        resource_pool_id=pool.id,
        name="capacity-run",
        status=run_status,
        requested_concurrency=1,
        total_cases=count,
        queued_count=count,
        idempotency_key=f"cap-{uuid4().hex}",
        created_by="agent",
    )
    session.add(run)
    session.flush()
    for ordinal in range(count):
        case = EvalCaseRun(
            tenant_id=scope["tenant_id"],
            eval_run_id=run.id,
            dataset_case_id=f"case-{ordinal}",
            case_key=f"case-{ordinal}",
            ordinal=ordinal,
        )
        session.add(case)
        session.flush()
        session.add(
            EvalCaseAttempt(
                tenant_id=scope["tenant_id"],
                eval_case_run_id=case.id,
                attempt_no=1,
                status="queued",
                resource_pool_id=pool.id,
            )
        )
    session.flush()
    return run


def _runner(
    session: Session,
    scope: dict[str, str],
    pool: ResourcePool,
    *,
    name: str,
    status: str = "online",
    heartbeat_at: datetime | None = None,
    registered_at: datetime | None = None,
    with_instance: bool = True,
    instance_status: str = "online",
) -> tuple[Runner, ProvisionedInstance | None]:
    runner = Runner(
        tenant_id=scope["tenant_id"],
        resource_pool_id=pool.id,
        provider_runner_id=name,
        name=name,
        labels_json=["self-hosted", "spot"],
        status=status,
        last_heartbeat_at=heartbeat_at or datetime.now(UTC),
        registered_at=registered_at or datetime.now(UTC),
    )
    session.add(runner)
    session.flush()
    instance = None
    if with_instance:
        instance = ProvisionedInstance(
            tenant_id=scope["tenant_id"],
            resource_pool_id=pool.id,
            provider_instance_id=f"fake-instance-{uuid4().hex[:8]}",
            runner_id=runner.id,
            status=instance_status,
            spot_strategy="spot",
            cost_json={},
        )
        session.add(instance)
        session.flush()
    return runner, instance


def _utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; compare them as UTC instants."""

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _event_types(session: Session) -> list[str]:
    return [
        event.event_type
        for event in session.scalars(
            select(ResourceEvent).order_by(ResourceEvent.created_at, ResourceEvent.id)
        )
    ]


def _events(session: Session, event_type: str) -> list[ResourceEvent]:
    return list(
        session.scalars(
            select(ResourceEvent)
            .where(ResourceEvent.event_type == event_type)
            .order_by(ResourceEvent.created_at, ResourceEvent.id)
        )
    )


# ---------------------------------------------------------------------------
# Gap math and state snapshot
# ---------------------------------------------------------------------------


def test_capacity_gap_counts_queued_idle_and_starting(
    session: Session, scope: dict[str, str]
) -> None:
    pool = _spot_pool_with_connection(
        session,
        scope,
        max_capacity=8,
        instance_config_json={"instance_type": "ecs.g7.large", "runners_per_instance": 2},
    )
    _queued_cases(session, scope, pool, 5)
    _runner(session, scope, pool, name="idle-1")
    session.add(
        ProvisionedInstance(
            tenant_id=scope["tenant_id"],
            resource_pool_id=pool.id,
            provider_instance_id="starting-1",
            status="creating",
            cost_json={},
        )
    )
    session.flush()
    now = datetime.now(UTC)

    state = capacity_control.pool_capacity_state(
        session, pool, settings=get_settings(), now=now, provisioner=FakeProvisioner()
    )

    assert state["queued_attempts"] == 5
    assert state["idle_runners"] == 1
    assert state["starting_instances"] == 1
    assert state["runners_per_instance"] == 2
    # 5 queued - 1 idle - 1 starting instance * 2 runners = 2
    assert state["capacity_gap"] == 2
    assert state["current_size"] == 2  # 1 online runner + 1 starting instance
    assert state["desired_capacity"] == 3  # 2 + ceil(2 / 2)
    assert state["checked_at"] == now.isoformat(timespec="microseconds")
    # A read-only snapshot never writes the pool or the event log.
    assert pool.last_checked_at is None
    assert _event_types(session) == []


def test_capacity_gap_counts_pending_attempts_before_dispatch(
    session: Session, scope: dict[str, str]
) -> None:
    """M5-08: an empty spot pool must bootstrap from a not-yet-dispatched run.

    The dispatcher's capacity gate leaves attempts ``pending`` until a runner
    exists, so the controller must count them as queue demand; otherwise the
    pool never creates the first instance and the run deadlocks.
    """

    pool = _spot_pool_with_connection(session, scope, max_capacity=5)
    _queued_cases(session, scope, pool, 5)
    for attempt in session.scalars(
        select(EvalCaseAttempt).where(EvalCaseAttempt.resource_pool_id == pool.id)
    ):
        attempt.status = "pending"
    session.flush()

    state = capacity_control.pool_capacity_state(
        session, pool, settings=get_settings(), now=datetime.now(UTC), provisioner=FakeProvisioner()
    )

    assert state["queued_attempts"] == 5
    assert state["capacity_gap"] == 5
    assert state["desired_capacity"] == 5


def test_desired_capacity_is_capped_by_max_capacity(
    session: Session, scope: dict[str, str]
) -> None:
    pool = _spot_pool_with_connection(session, scope, max_capacity=2)
    _queued_cases(session, scope, pool, 9)

    state = capacity_control.pool_capacity_state(
        session, pool, settings=get_settings(), provisioner=FakeProvisioner()
    )

    assert state["capacity_gap"] == 9
    assert state["desired_capacity"] == 2
    assert state["capacity_reason"] == capacity_control.REASON_CAPACITY_SCALE_UP


# ---------------------------------------------------------------------------
# Scale up
# ---------------------------------------------------------------------------


def test_scale_up_creates_exactly_one_batch(
    session: Session, scope: dict[str, str], configure_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_settings(EVAL_CAPACITY_BATCH_SIZE="2")
    monkeypatch.setenv("EVAL_CAPACITY_CHECK_SECONDS", "120")
    get_settings.cache_clear()
    settings = get_settings()
    fake = FakeProvisioner(price_usd=Decimal("0.1000"))
    pool = _spot_pool_with_connection(session, scope, max_capacity=6)
    _queued_cases(session, scope, pool, 8)
    now = datetime.now(UTC)

    state = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=now, provisioner=fake
    )

    assert state["capacity_reason"] == capacity_control.REASON_CAPACITY_SCALE_UP
    assert state["scaled_up"] == 2  # batch size wins over the 6-slot headroom
    assert state["capacity_gap"] == 8
    assert _events(session, capacity_control.EVENT_CAPACITY_UPDATED)
    instances = list(
        session.scalars(
            select(ProvisionedInstance).where(ProvisionedInstance.resource_pool_id == pool.id)
        )
    )
    assert len(instances) == 2
    assert {instance.status for instance in instances} == {"requested"}
    assert {instance.provider_instance_id for instance in instances} == {
        "fake-instance-1",
        "fake-instance-2",
    }
    assert pool.capacity_reason == capacity_control.REASON_CAPACITY_SCALE_UP
    assert pool.price_usd == Decimal("0.1000")
    assert _utc(pool.last_checked_at) == now
    assert _utc(pool.next_check_at) == now + timedelta(seconds=settings.eval_capacity_check_seconds)

    # Next tick: starting instances count against the gap, so the remaining
    # headroom (max 6 - 2 current) is what the second batch may add.
    more = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=now + timedelta(seconds=120), provisioner=fake
    )
    assert more["scaled_up"] == 2  # min(6 - 2, 8 - 2, 2)


def test_capacity_event_is_deduped_across_ticks(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    fake = FakeProvisioner(price_usd=Decimal("0.5000"))
    pool = _spot_pool_with_connection(
        session, scope, max_capacity=3, price_threshold_usd=Decimal("0.1000")
    )
    _queued_cases(session, scope, pool, 2)
    now = datetime.now(UTC)

    first = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=now, provisioner=fake
    )
    second = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=now + timedelta(seconds=60), provisioner=fake
    )

    assert first["capacity_reason"] == capacity_control.REASON_PRICE_ABOVE_THRESHOLD
    assert second["capacity_reason"] == capacity_control.REASON_PRICE_ABOVE_THRESHOLD
    assert len(_events(session, capacity_control.EVENT_CAPACITY_UPDATED)) == 1
    assert _utc(pool.last_checked_at) == now + timedelta(seconds=60)


# ---------------------------------------------------------------------------
# Waiting states never fail cases
# ---------------------------------------------------------------------------


def test_price_above_threshold_blocks_creation(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    fake = FakeProvisioner(price_usd=Decimal("0.5000"))
    pool = _spot_pool_with_connection(
        session, scope, min_capacity=0, max_capacity=4, price_threshold_usd=Decimal("0.2000")
    )
    run = _queued_cases(session, scope, pool, 3)
    now = datetime.now(UTC)

    state = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=now, provisioner=fake
    )

    assert state["capacity_reason"] == capacity_control.REASON_PRICE_ABOVE_THRESHOLD
    assert state["price_usd"] == "0.5000"
    assert state["price_threshold_usd"] == "0.2000"
    assert state["checked_at"] == now.isoformat(timespec="microseconds")
    assert state["next_check_at"] == (now + timedelta(seconds=settings.eval_capacity_check_seconds)).isoformat(
        timespec="microseconds"
    )
    assert pool.price_usd == Decimal("0.5000")
    assert pool.price_threshold_usd == Decimal("0.2000")
    assert pool.capacity_reason == capacity_control.REASON_PRICE_ABOVE_THRESHOLD

    assert session.scalar(select(func.count()).select_from(ProvisionedInstance)) == 0
    assert _events(session, capacity_control.EVENT_CAPACITY_UPDATED)
    # No provider call was made, so nothing failed.
    assert _events(session, capacity_control.EVENT_PROVISIONING_FAILED) == []
    assert run.status == "queued"
    assert (
        session.scalar(
            select(func.count())
            .select_from(EvalCaseAttempt)
            .where(EvalCaseAttempt.status == "queued")
        )
        == 3
    )


def test_inventory_shortage_keeps_cases_queued(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    fake = FakeProvisioner(price_usd=Decimal("0.1000"), available=False)
    pool = _spot_pool_with_connection(session, scope, max_capacity=3)
    run = _queued_cases(session, scope, pool, 3)

    state = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=datetime.now(UTC), provisioner=fake
    )

    assert state["capacity_reason"] == capacity_control.REASON_INVENTORY_INSUFFICIENT
    assert state["price_available"] is False
    assert pool.capacity_reason == capacity_control.REASON_INVENTORY_INSUFFICIENT
    assert session.scalar(select(func.count()).select_from(ProvisionedInstance)) == 0
    failures = _events(session, capacity_control.EVENT_PROVISIONING_FAILED)
    assert len(failures) == 1
    assert failures[0].payload_json["reason"] == capacity_control.REASON_INVENTORY_INSUFFICIENT
    assert run.status == "queued"
    assert session.get(EvalRun, run.id).status == "queued"


def test_create_failure_is_recorded_not_raised(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    fake = FakeProvisioner(price_usd=Decimal("0.1000"), create_error="quota_exceeded")
    pool = _spot_pool_with_connection(session, scope, max_capacity=3)
    run = _queued_cases(session, scope, pool, 2)

    state = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=datetime.now(UTC), provisioner=fake
    )

    assert state["capacity_reason"] == capacity_control.REASON_PROVISIONING_FAILED
    assert pool.capacity_reason == capacity_control.REASON_PROVISIONING_FAILED
    assert session.scalar(select(func.count()).select_from(ProvisionedInstance)) == 0
    failures = _events(session, capacity_control.EVENT_PROVISIONING_FAILED)
    assert len(failures) == 1
    assert failures[0].payload_json["code"] == "quota_exceeded"
    # A provisioning failure never fails an evaluation case.
    assert run.status == "queued"
    assert session.scalar(
        select(func.count())
        .select_from(EvalCaseAttempt)
        .where(EvalCaseAttempt.status.in_(("failed", "timeout", "cancelled")))
    ) == 0


# ---------------------------------------------------------------------------
# Scale down
# ---------------------------------------------------------------------------


class _RecordingProvisioner(FakeProvisioner):
    """Records the runner state observed at release time (ordering proof)."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.releases: list[dict[str, object]] = []

    def release(self, *, session: Session, instance: ProvisionedInstance, now: datetime) -> None:
        runner = session.get(Runner, instance.runner_id) if instance.runner_id else None
        self.releases.append(
            {
                "instance_id": instance.id,
                "provider_instance_id": instance.provider_instance_id,
                "runner_status": runner.status if runner is not None else None,
                "deregistered_at": runner.deregistered_at if runner is not None else None,
            }
        )
        super().release(session=session, instance=instance, now=now)


def test_scale_down_drains_runner_before_release(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    fake = _RecordingProvisioner(price_usd=Decimal("0.1000"))
    pool = _spot_pool_with_connection(session, scope, min_capacity=0, max_capacity=4)
    now = datetime.now(UTC)
    older, _ = _runner(
        session,
        scope,
        pool,
        name="runner-older",
        registered_at=now - timedelta(hours=2),
        heartbeat_at=now,
    )
    newer, _ = _runner(
        session,
        scope,
        pool,
        name="runner-newer",
        registered_at=now - timedelta(hours=1),
        heartbeat_at=now,
    )
    instances = list(
        session.scalars(
            select(ProvisionedInstance).where(ProvisionedInstance.resource_pool_id == pool.id)
        )
    )
    instance_ids = {instance.runner_id: instance.id for instance in instances}
    provider_ids = {instance.runner_id: instance.provider_instance_id for instance in instances}

    state = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=now, provisioner=fake
    )

    assert state["capacity_reason"] == capacity_control.REASON_CAPACITY_SCALE_DOWN
    assert state["scaled_down"] == 2
    # Newest runner goes first.
    assert [item["provider_instance_id"] for item in fake.releases] == [
        provider_ids[newer.id],
        provider_ids[older.id],
    ]
    # Every release happened only after the runner was deregistered.
    assert {item["runner_status"] for item in fake.releases} == {"offline"}
    assert all(item["deregistered_at"] == now for item in fake.releases)
    for runner in (older, newer):
        assert runner.status == "offline"
        assert runner.deregistered_at == now
    instances = list(
        session.scalars(
            select(ProvisionedInstance).where(ProvisionedInstance.resource_pool_id == pool.id)
        )
    )
    assert {instance.status for instance in instances} == {"released"}
    assert {instance.released_at for instance in instances} == {now}

    scaled = _events(session, capacity_control.EVENT_CAPACITY_SCALED_DOWN)
    assert [event.payload_json["order"] for event in scaled] == [1, 2]
    assert [event.payload_json["runner_id"] for event in scaled] == [newer.id, older.id]
    assert {event.payload_json["instance_id"] for event in scaled} == set(instance_ids.values())


def test_scale_down_respects_min_capacity(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    fake = _RecordingProvisioner()
    pool = _spot_pool_with_connection(session, scope, min_capacity=2, max_capacity=4)
    for index in range(3):
        _runner(
            session,
            scope,
            pool,
            name=f"runner-{index}",
            registered_at=datetime.now(UTC) - timedelta(minutes=index + 1),
        )

    state = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=datetime.now(UTC), provisioner=fake
    )

    assert state["scaled_down"] == 1
    assert len(fake.releases) == 1


def test_release_failure_keeps_instance_and_continues(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    fake = _RecordingProvisioner(release_error="release_denied")
    pool = _spot_pool_with_connection(session, scope, min_capacity=0, max_capacity=4)
    now = datetime.now(UTC)
    runner, instance = _runner(session, scope, pool, name="runner-1", heartbeat_at=now)
    assert instance is not None

    state = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=now, provisioner=fake
    )

    # A failed release must not count as a scale-down: the instance still costs.
    assert state["scaled_down"] == 0
    assert runner.status == "offline"
    assert runner.deregistered_at == now
    assert instance.status == "draining"  # parked for the retry pass
    assert instance.released_at is None
    failures = _events(session, capacity_control.EVENT_RELEASE_FAILED)
    assert len(failures) == 1
    assert failures[0].payload_json["instance_id"] == instance.id
    assert failures[0].payload_json["runner_id"] == runner.id
    assert failures[0].payload_json["code"] == "release_denied"

    # The next tick (release now succeeds) drains the pending instance exactly
    # once; the runner already being offline no longer hides it.
    fake.release_error = None
    later = now + timedelta(seconds=1)
    state = capacity_control.reconcile_pool(
        session, pool, settings=settings, now=later, provisioner=fake
    )
    assert state["scaled_down"] == 0
    assert instance.status == "released"
    assert _utc(instance.released_at) == later
    released = _events(session, capacity_control.EVENT_CAPACITY_RELEASED)
    assert len(released) == 1


# ---------------------------------------------------------------------------
# reconcile_pools sweep
# ---------------------------------------------------------------------------


def test_reconcile_pools_skips_fixed_pools(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    fixed = _pool(
        session,
        scope,
        name="fixed-pool",
        provider_key="fixed",
        provisioning_mode="fixed",
        min_capacity=0,
        max_capacity=1,
        instance_config_json={},
    )
    _queued_cases(session, scope, fixed, 2)
    now = datetime.now(UTC)

    counts = capacity_control.reconcile_pools(session, settings=settings, now=now)

    assert counts == {
        "scanned": 0,
        "scaled_up": 0,
        "scaled_down": 0,
        "waiting_price": 0,
        "errors": 0,
    }
    assert fixed.last_checked_at is None
    assert session.scalar(select(func.count()).select_from(ProvisionedInstance)) == 0
    assert _event_types(session) == []


def test_reconcile_pools_counts_one_spot_pool(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    fake = FakeProvisioner(price_usd=Decimal("0.1000"))
    pool = _spot_pool_with_connection(session, scope, max_capacity=3)
    _queued_cases(session, scope, pool, 2)
    now = datetime.now(UTC)

    counts = capacity_control.reconcile_pools(
        session, settings=settings, now=now, provisioners={fake.provider: fake}
    )

    assert counts["scanned"] == 1
    assert counts["scaled_up"] == 1
    assert counts["scaled_down"] == 0
    assert counts["waiting_price"] == 0
    assert counts["errors"] == 0
    assert _utc(pool.last_checked_at) == now


def test_reconcile_pools_handles_missing_provisioner_and_due_filter(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()
    pool = _spot_pool_with_connection(session, scope, max_capacity=3)
    _queued_cases(session, scope, pool, 2)
    now = datetime.now(UTC)

    # No provisioner registered for the pool provider: it waits, nothing breaks.
    counts = capacity_control.reconcile_pools(session, settings=settings, now=now)
    assert counts["scanned"] == 1
    assert counts["errors"] == 0
    assert pool.capacity_reason == capacity_control.REASON_PROVISIONER_UNAVAILABLE

    # next_check_at now sits in the future, so ticks before it skip the pool.
    skipped = capacity_control.reconcile_pools(
        session, settings=settings, now=now, now_after=now
    )
    assert skipped["scanned"] == 0
    due = capacity_control.reconcile_pools(
        session,
        settings=settings,
        now=now + timedelta(seconds=600),
        now_after=now + timedelta(seconds=600),
    )
    assert due["scanned"] == 1


def test_reconcile_pools_keeps_going_after_a_pool_error(
    session: Session, scope: dict[str, str]
) -> None:
    settings = get_settings()

    class _ExplodingProvisioner(FakeProvisioner):
        def quote(self, *, connection, pool, now):  # type: ignore[no-untyped-def]
            raise RuntimeError("provider exploded")

    exploding = _ExplodingProvisioner()
    healthy_provider = FakeProvisioner(provider="other")
    _spot_pool_with_connection(session, scope, max_capacity=3)
    healthy = _spot_pool_with_connection(
        session, scope, provider="other", max_capacity=3
    )
    # A failing pool rolls its transaction back; fixtures must be durable first
    # or the rollback would also discard the healthy pool's rows.
    session.commit()
    now = datetime.now(UTC)

    counts = capacity_control.reconcile_pools(
        session,
        settings=settings,
        now=now,
        provisioners={exploding.provider: exploding, "other": healthy_provider},
    )

    assert counts["scanned"] == 2
    assert counts["errors"] == 1
    assert _utc(healthy.last_checked_at) == now


# ---------------------------------------------------------------------------
# Worker wiring
# ---------------------------------------------------------------------------


def test_worker_tick_runs_one_capacity_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.worker import main as worker_main

    calls: list[dict[str, int]] = []

    def _fake_reconcile(session, *, settings, now=None, provisioners=None, now_after=None):
        calls.append({"scanned": 1})
        return {
            "scanned": 1,
            "scaled_up": 0,
            "scaled_down": 0,
            "waiting_price": 0,
            "errors": 0,
        }

    class _Stop(Exception):
        pass

    def _stop_loop(_seconds: float) -> None:
        raise _Stop

    monkeypatch.setattr(capacity_control, "reconcile_pools", _fake_reconcile)
    monkeypatch.setattr(worker_main.time, "sleep", _stop_loop)

    with pytest.raises(_Stop):
        worker_main.main()

    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "worker: capacity scanned=1 scaled_up=0 scaled_down=0 waiting_price=0 errors=0" in output


# ---------------------------------------------------------------------------
# Event log hygiene
# ---------------------------------------------------------------------------


def test_resource_event_dedupe_key_is_unique_per_tenant(
    session: Session, scope: dict[str, str]
) -> None:
    for _ in range(2):
        session.add(
            ResourceEvent(
                tenant_id=scope["tenant_id"],
                event_type=capacity_control.EVENT_CAPACITY_UPDATED,
                payload_json={},
                dedupe_key="pool:state",
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

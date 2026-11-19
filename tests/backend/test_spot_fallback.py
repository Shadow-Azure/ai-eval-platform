"""M8-46 (#311): spot supply failures fall back to fixed/on-demand capacity.

The tests pin the three observable contracts:

* consecutive provider-side spot reclaims move the run to the configured fixed
  pool before the retry manifest is built, and the run carries an ``已回退``
  snapshot instead of ending as ``spot_retry_exhausted``;
* repeated inventory/price supply blocks in the capacity controller do the
  same for a shard that never reached a spot runner;
* the cost cap, a successful case and the existing no-fallback path keep their
  documented behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.config import get_settings
from app.models import (
    Connection,
    Credential,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    Outbox,
    ResourceEvent,
    ResourcePool,
)
from app.services import capacity_control, case_attempts, spot_fallback, workbench
from app.services.provisioning.fake import FakeProvisioner
from sqlalchemy import select
from sqlalchemy.orm import Session

BASE_NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)


def _fixed_pool(
    session: Session,
    run: EvalRun,
    *,
    name: str = "fixed-fallback",
    reference_price: str | None = "1.0000",
    labels: list[str] | None = None,
) -> ResourcePool:
    config: dict = {}
    if reference_price is not None:
        config["reference_price_cny"] = reference_price
    pool = ResourcePool(
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        name=name,
        provider_key="fixed",
        provisioning_mode="fixed",
        min_capacity=0,
        max_capacity=5,
        instance_config_json=config,
        labels_json={"runs_on": labels or ["self-hosted", "fixed-eval"]},
        status="active",
    )
    session.add(pool)
    session.flush()
    return pool


def _credential(session: Session, run: EvalRun) -> Credential:
    credential = Credential(
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        kind="resource",
        provider="fake",
        ciphertext="ciphertext-must-never-leak",
        key_version="local:test-key",
        secret_mask="ak-***mask",
    )
    session.add(credential)
    session.flush()
    return credential


def _spot_pool(
    session: Session,
    run: EvalRun,
    *,
    fallback_pool_id: str | None,
    fallback_after: int = 3,
    fallback_max_price_cny: str | None = None,
    price_threshold_cny: Decimal | None = None,
    labels: list[str] | None = None,
) -> ResourcePool:
    connection = Connection(
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        name=f"conn-{run.id[:8]}",
        provider="fake",
        region="cn-hangzhou",
        credential_id=_credential(session, run).id,
        capabilities_json={"capabilities": ["ecs:spot", "ecs:inventory"]},
        status="verified",
    )
    session.add(connection)
    session.flush()
    config: dict = {
        "instance_type": "ecs.g7.large",
        "runners_per_instance": 1,
    }
    if fallback_pool_id:
        config["fallback_pool_id"] = fallback_pool_id
        config["fallback_after"] = fallback_after
        if fallback_max_price_cny:
            config["fallback_max_price_cny"] = fallback_max_price_cny
    pool = ResourcePool(
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        connection_id=connection.id,
        name=f"spot-{run.id[:8]}",
        provider_key="fake",
        provisioning_mode="spot",
        min_capacity=0,
        max_capacity=5,
        instance_config_json=config,
        price_threshold_cny=price_threshold_cny,
        labels_json={"runs_on": labels or ["self-hosted", "spot-eval"]},
        status="active",
    )
    session.add(pool)
    session.flush()
    return pool


def _bind_run(
    session: Session,
    run: EvalRun,
    *,
    spot: ResourcePool,
    max_attempts: int = 4,
) -> None:
    run.resource_pool_id = spot.id
    run.max_attempts = max_attempts
    run.resource_pool_snapshot_json = {}
    session.commit()


def _attempts(session: Session, case_run_id: str) -> list[EvalCaseAttempt]:
    return list(
        session.scalars(
            select(EvalCaseAttempt)
            .where(EvalCaseAttempt.eval_case_run_id == case_run_id)
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )


def _outbox_for_attempt(session: Session, run_id: str, attempt_no: int) -> Outbox:
    rows = list(
        session.scalars(
            select(Outbox).where(
                Outbox.aggregate_id == run_id,
                Outbox.topic == "eval.shard.dispatch",
            )
        )
    )
    return next(row for row in rows if row.payload_json.get("attempt") == attempt_no)


def test_three_spot_reclaims_switch_run_to_fixed_before_retry(eval_env) -> None:
    env = eval_env
    session: Session = env["session"]
    case_run_id = env["cases"]["case-a"]
    run = session.get(EvalRun, session.get(EvalCaseRun, case_run_id).eval_run_id)
    assert run is not None
    fixed = _fixed_pool(session, run)
    spot = _spot_pool(session, run, fallback_pool_id=fixed.id, fallback_after=3)
    _bind_run(session, run, spot=spot, max_attempts=4)
    for index in range(3):
        assert case_attempts.handle_spot_interruption(
            session,
            case_run_id=case_run_id,
            reason="provider_stopped",
            provider_state="Stopped",
            now=BASE_NOW + timedelta(minutes=index),
        ) is True

    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.resource_pool_id == fixed.id
    state = run.resource_pool_snapshot_json["spot_fallback"]
    assert state["active"] is True
    assert state["from_pool_id"] == spot.id
    assert state["to_pool_id"] == fixed.id
    assert state["consecutive_failures"] == 3
    assert state["reason"] == spot_fallback.REASON_PROVIDER_RECLAIM
    assert state["to_pool_name"] == fixed.name

    attempts = _attempts(session, case_run_id)
    assert [attempt.attempt_no for attempt in attempts] == [1, 2, 3, 4]
    # The third reclaim activates the fallback, so attempt 4 is created on the
    # fixed pool and its manifest targets the fixed runner labels.
    assert attempts[-1].resource_pool_id == fixed.id
    retry_row = _outbox_for_attempt(session, run.id, 4)
    assert retry_row.payload_json["client_payload"]["runs_on"] == [
        "self-hosted",
        "fixed-eval",
    ]

    case = session.get(EvalCaseRun, case_run_id)
    assert case is not None
    assert case.execution_status == "queued"
    assert case.error_code != "spot_retry_exhausted"
    assert "已回退到" in (case.error_summary or "")

    events = list(
        session.scalars(select(EvalEvent).where(EvalEvent.eval_run_id == run.id))
    )
    fallback_event = next(event for event in events if event.event_type == "eval.spot_fallback")
    assert fallback_event.payload_json["to_pool_id"] == fixed.id
    assert fallback_event.payload_json["retargeted_attempts"] >= 0
    resource_events = list(
        session.scalars(
            select(ResourceEvent).where(
                ResourceEvent.event_type == spot_fallback.EVENT_RESOURCE_FALLBACK
            )
        )
    )
    assert {event.resource_pool_id for event in resource_events} == {
        spot.id,
        fixed.id,
    }

    config = workbench.config_snapshot(session, run)
    assert config["resource_pool"]["id"] == fixed.id
    assert config["spot_fallback"]["active"] is True
    assert config["spot_fallback"]["to_pool_name"] == fixed.name


def test_fallback_cost_cap_keeps_spot_retry(eval_env) -> None:
    env = eval_env
    session: Session = env["session"]
    case_run_id = env["cases"]["case-a"]
    run = session.get(EvalRun, session.get(EvalCaseRun, case_run_id).eval_run_id)
    assert run is not None
    fixed = _fixed_pool(session, run, reference_price="2.0000")
    spot = _spot_pool(
        session,
        run,
        fallback_pool_id=fixed.id,
        fallback_after=2,
        fallback_max_price_cny="1.0000",
    )
    _bind_run(session, run, spot=spot, max_attempts=3)

    assert case_attempts.handle_spot_interruption(
        session, case_run_id=case_run_id, now=BASE_NOW
    ) is True
    assert case_attempts.handle_spot_interruption(
        session, case_run_id=case_run_id, now=BASE_NOW + timedelta(minutes=1)
    ) is True

    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.resource_pool_id == spot.id
    state = run.resource_pool_snapshot_json["spot_fallback"]
    assert state.get("active") is not True
    assert state["consecutive_failures"] == 2
    assert state["suppressed_reason"] == "fallback_cost_cap_exceeded"

    attempts = _attempts(session, case_run_id)
    assert [attempt.attempt_no for attempt in attempts] == [1, 2, 3]
    retry_row = _outbox_for_attempt(session, run.id, 3)
    assert retry_row.payload_json["client_payload"]["runs_on"] == [
        "self-hosted",
        "spot-eval",
    ]
    events = list(
        session.scalars(
            select(ResourceEvent).where(
                ResourceEvent.event_type == spot_fallback.EVENT_RESOURCE_SUPPRESSED
            )
        )
    )
    assert events and events[-1].payload_json["reason"] == "fallback_cost_cap_exceeded"


def test_supply_block_falls_back_after_threshold(session: Session) -> None:
    from app.seed import ensure_default_scope

    tenant, _workspace, project = ensure_default_scope(session)
    run = EvalRun(
        tenant_id=tenant.id,
        project_id=project.id,
        name="supply-block-run",
        status="queued",
        requested_concurrency=1,
        total_cases=1,
        queued_count=1,
        idempotency_key="supply-block-run",
        created_by="agent",
    )
    session.add(run)
    session.flush()
    fixed = _fixed_pool(session, run)
    spot = _spot_pool(session, run, fallback_pool_id=fixed.id, fallback_after=2)
    run.resource_pool_id = spot.id
    case = EvalCaseRun(
        tenant_id=tenant.id,
        eval_run_id=run.id,
        dataset_case_id="supply-case",
        case_key="supply-case",
        ordinal=1,
    )
    session.add(case)
    session.flush()
    session.add(
        EvalCaseAttempt(
            tenant_id=tenant.id,
            eval_case_run_id=case.id,
            attempt_no=1,
            status="pending",
            resource_pool_id=spot.id,
        )
    )
    session.commit()

    provisioner = FakeProvisioner(available=False)
    for index in range(2):
        state = capacity_control.reconcile_pool(
            session,
            spot,
            settings=get_settings(),
            now=BASE_NOW + timedelta(seconds=30 * index),
            provisioner=provisioner,
        )
        assert state["capacity_reason"] == capacity_control.REASON_INVENTORY_INSUFFICIENT
        session.commit()

    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.resource_pool_id == fixed.id
    snapshot = run.resource_pool_snapshot_json["spot_fallback"]
    assert snapshot["active"] is True
    assert snapshot["reason"] == spot_fallback.REASON_INVENTORY
    assert snapshot["consecutive_supply_blocks"] == 2

    attempts = _attempts(session, case.id)
    assert attempts[0].resource_pool_id == fixed.id


def test_price_above_threshold_falls_back_with_supply_reason(session: Session) -> None:
    from app.seed import ensure_default_scope

    tenant, _workspace, project = ensure_default_scope(session)
    run = EvalRun(
        tenant_id=tenant.id,
        project_id=project.id,
        name="price-block-run",
        status="queued",
        requested_concurrency=1,
        total_cases=1,
        queued_count=1,
        idempotency_key="price-block-run",
        created_by="agent",
    )
    session.add(run)
    session.flush()
    fixed = _fixed_pool(session, run)
    spot = _spot_pool(
        session,
        run,
        fallback_pool_id=fixed.id,
        fallback_after=1,
        price_threshold_cny=Decimal("1.0000"),
    )
    run.resource_pool_id = spot.id
    case = EvalCaseRun(
        tenant_id=tenant.id,
        eval_run_id=run.id,
        dataset_case_id="price-case",
        case_key="price-case",
        ordinal=1,
    )
    session.add(case)
    session.flush()
    session.add(
        EvalCaseAttempt(
            tenant_id=tenant.id,
            eval_case_run_id=case.id,
            attempt_no=1,
            status="queued",
            resource_pool_id=spot.id,
        )
    )
    session.commit()

    state = capacity_control.reconcile_pool(
        session,
        spot,
        settings=get_settings(),
        now=BASE_NOW,
        provisioner=FakeProvisioner(price_cny=Decimal("9.0000")),
    )
    assert state["capacity_reason"] == capacity_control.REASON_PRICE_ABOVE_THRESHOLD
    session.commit()

    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.resource_pool_id == fixed.id
    assert run.resource_pool_snapshot_json["spot_fallback"]["reason"] == (
        spot_fallback.REASON_PRICE
    )


def test_success_resets_the_consecutive_streak(eval_env) -> None:
    env = eval_env
    session: Session = env["session"]
    case_run_id = env["cases"]["case-a"]
    run = session.get(EvalRun, session.get(EvalCaseRun, case_run_id).eval_run_id)
    assert run is not None
    fixed = _fixed_pool(session, run)
    spot = _spot_pool(session, run, fallback_pool_id=fixed.id, fallback_after=3)
    _bind_run(session, run, spot=spot, max_attempts=4)

    assert case_attempts.handle_spot_interruption(
        session, case_run_id=case_run_id, now=BASE_NOW
    ) is True
    session.refresh(run)
    assert run.resource_pool_snapshot_json["spot_fallback"]["consecutive_failures"] == 1

    spot_fallback.reset_after_success(session, run=run, now=BASE_NOW)
    session.commit()
    session.refresh(run)
    assert run.resource_pool_snapshot_json["spot_fallback"]["consecutive_failures"] == 0
    assert run.resource_pool_snapshot_json["spot_fallback"]["last_success_at"]


def test_validate_fallback_config_rejects_bad_target(eval_env) -> None:
    env = eval_env
    session: Session = env["session"]
    run = session.get(EvalRun, env["run_id"])
    assert run is not None
    fixed = _fixed_pool(session, run)
    spot = _spot_pool(session, run, fallback_pool_id=fixed.id)

    spot_fallback.validate_fallback_config(
        session,
        tenant_id=run.tenant_id,
        pool_id=spot.id,
        provisioning_mode="spot",
        instance_config={"fallback_pool_id": fixed.id, "fallback_after": 3},
    )

    with pytest.raises(spot_fallback.FallbackConfigError) as missing:
        spot_fallback.validate_fallback_config(
            session,
            tenant_id=run.tenant_id,
            pool_id=spot.id,
            provisioning_mode="spot",
            instance_config={"fallback_pool_id": "missing"},
        )
    assert missing.value.code == "spot_fallback_pool_not_found"

    with pytest.raises(spot_fallback.FallbackConfigError) as self_target:
        spot_fallback.validate_fallback_config(
            session,
            tenant_id=run.tenant_id,
            pool_id=fixed.id,
            provisioning_mode="fixed",
            instance_config={"fallback_pool_id": fixed.id},
        )
    assert self_target.value.code == "spot_fallback_requires_spot_pool"

    unpriced = _fixed_pool(session, run, name="unpriced", reference_price=None)
    with pytest.raises(spot_fallback.FallbackConfigError) as cost_unknown:
        spot_fallback.validate_fallback_config(
            session,
            tenant_id=run.tenant_id,
            pool_id=spot.id,
            provisioning_mode="spot",
            instance_config={
                "fallback_pool_id": unpriced.id,
                "fallback_max_price_cny": "1.0000",
            },
        )
    assert cost_unknown.value.code == "spot_fallback_cost_unknown"

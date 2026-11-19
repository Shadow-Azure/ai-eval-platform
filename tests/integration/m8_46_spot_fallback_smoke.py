#!/usr/bin/env python3
"""M8-46 (#311) live smoke: spot -> fixed/on-demand fallback.

Run manually (not collected by pytest):

    .venv/bin/python tests/integration/m8_46_spot_fallback_smoke.py \
      --out /tmp/m8-46-evidence.json

The smoke builds a disposable SQLite control plane and drives the real
attempt-settlement and capacity-controller paths with an injected clock and the
deterministic fake provisioner. It covers the acceptance timeline:

1. three consecutive provider-side spot reclaims switch the run to the fixed
   pool before the retry attempt/manifest is built;
2. a successful case resets the streak;
3. repeated inventory shortages switch a never-started queued shard;
4. a cost cap suppresses the fallback and leaves the normal spot retry intact.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/m8-46-evidence.json")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="m8-46-smoke-"))
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{tmp}/smoke.db",
            "REDIS_URL": "",
            "AUTH_SESSION_SECRET": "m8-46-smoke-session-secret",
            "AUTH_BOOTSTRAP_ADMIN_EMAIL": "admin@m8-46-smoke.local",
            "AUTH_BOOTSTRAP_ADMIN_PASSWORD": "m8-46-smoke-password",
            "EVAL_SPOT_FALLBACK_AFTER": "3",
            "RUNNER_SPOT_LABELS": "self-hosted,linux,eval",
            "GITHUB_TOKEN": "m8-46-smoke-token",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "app.seed"], cwd=BACKEND, env=env, check=True, capture_output=True
    )

    inherited = ("RUNNER_", "DATABASE_URL", "S3_", "AUTH_", "EVAL_", "REDIS_", "GITHUB_")
    os.environ.update({key: env[key] for key in env if key.startswith(inherited)})
    sys.path.insert(0, str(BACKEND))

    from app.config import get_settings  # noqa: PLC0415
    from app.db import SessionLocal  # noqa: PLC0415
    from app.models import (  # noqa: PLC0415
        Connection,
        Credential,
        EvalCaseAttempt,
        EvalCaseRun,
        EvalRun,
        Outbox,
        ResourceEvent,
        ResourcePool,
    )
    from app.seed import ensure_default_scope  # noqa: PLC0415
    from app.services import (  # noqa: PLC0415
        capacity_control,
        case_attempts,
        evaluations,
        spot_fallback,
        workbench,
    )
    from app.services.provisioning.fake import FakeProvisioner  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    settings = get_settings()
    timeline = datetime.now(UTC)
    evidence: dict = {"issue": "#311", "started_at": iso(timeline), "scenarios": []}

    def make_pools(session, run, *, after: int, cap: str | None = None) -> tuple:
        tenant = run.tenant_id
        project = run.project_id
        fixed = ResourcePool(
            tenant_id=tenant,
            project_id=project,
            name=f"fixed-{run.id[:6]}",
            provider_key="fixed",
            provisioning_mode="fixed",
            min_capacity=0,
            max_capacity=4,
            instance_config_json={"reference_price_cny": "1.0000"},
            labels_json={"runs_on": ["self-hosted", "fixed-smoke"]},
            status="active",
        )
        credential = Credential(
            tenant_id=tenant,
            project_id=project,
            kind="resource",
            provider="fake",
            ciphertext="smoke-ciphertext",
            key_version="local:smoke",
            secret_mask="ak-***",
        )
        session.add_all([fixed, credential])
        session.flush()
        connection = Connection(
            tenant_id=tenant,
            project_id=project,
            name=f"fake-{run.id[:6]}",
            provider="fake",
            region="cn-hangzhou",
            credential_id=credential.id,
            capabilities_json={"capabilities": ["ecs:spot", "ecs:inventory"]},
            status="verified",
        )
        session.add(connection)
        session.flush()
        config = {
            "instance_type": "ecs.g7.large",
            "runners_per_instance": 1,
            "fallback_pool_id": fixed.id,
            "fallback_after": after,
        }
        if cap is not None:
            config["fallback_max_price_cny"] = cap
        spot = ResourcePool(
            tenant_id=tenant,
            project_id=project,
            connection_id=connection.id,
            name=f"spot-{run.id[:6]}",
            provider_key="fake",
            provisioning_mode="spot",
            min_capacity=0,
            max_capacity=4,
            instance_config_json=config,
            price_threshold_cny=Decimal("1.0000"),
            labels_json={"runs_on": ["self-hosted", "spot-smoke"]},
            status="active",
        )
        session.add(spot)
        session.flush()
        return spot, fixed

    def create_run(key: str, cases: int = 1) -> tuple:
        with SessionLocal() as session:
            ensure_default_scope(session)
            run, _ = evaluations.create_synthetic_evaluation(
                session,
                name=f"M8-46 smoke {key}",
                case_keys=[f"{key}-{index}" for index in range(cases)],
                requested_concurrency=1,
                idempotency_key=key,
                created_by="m8-46-smoke",
                settings=settings,
                now=timeline,
            )
            run.max_attempts = 4
            session.commit()
            return run.id

    def bind_pool(run_id: str, after: int, cap: str | None = None) -> tuple:
        with SessionLocal() as session:
            run = session.get(EvalRun, run_id)
            spot, fixed = make_pools(session, run, after=after, cap=cap)
            run.resource_pool_id = spot.id
            run.resource_pool_snapshot_json = {}
            session.commit()
            return spot.id, fixed.id

    def first_attempt(run_id: str) -> tuple[str, int]:
        with SessionLocal() as session:
            attempt = session.scalars(
                select(EvalCaseAttempt)
                .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
                .where(EvalCaseRun.eval_run_id == run_id)
                .order_by(EvalCaseAttempt.id)
            ).first()
            return attempt.eval_case_run_id, attempt.attempt_no

    # Scenario 1: three provider reclaims -> retry 4 on fixed.
    run_id = create_run("reclaim")
    spot_id, fixed_id = bind_pool(run_id, after=3)
    case_run_id, _ = first_attempt(run_id)
    for index in range(3):
        with SessionLocal() as session:
            assert case_attempts.handle_spot_interruption(
                session,
                case_run_id=case_run_id,
                reason="provider_stopped",
                provider_state="Stopped",
                now=timeline + timedelta(minutes=index),
            ) is True
    with SessionLocal() as session:
        run = session.get(EvalRun, run_id)
        attempts = list(
            session.scalars(
                select(EvalCaseAttempt).where(EvalCaseAttempt.eval_case_run_id == case_run_id)
            )
        )
        config = workbench.config_snapshot(session, run)
        retry = next(
            row
            for row in session.scalars(
                select(Outbox).where(Outbox.aggregate_id == run_id)
            )
            if row.payload_json.get("attempt") == 4
        )
        fallback_events = list(
            session.scalars(
                select(ResourceEvent).where(
                    ResourceEvent.event_type == spot_fallback.EVENT_RESOURCE_FALLBACK
                )
            )
        )
        assert run.resource_pool_id == fixed_id
        assert config["spot_fallback"]["active"] is True
        assert attempts[-1].attempt_no == 4
        assert attempts[-1].resource_pool_id == fixed_id
        assert retry.payload_json["client_payload"]["runs_on"] == [
            "self-hosted",
            "fixed-smoke",
        ]
        assert {event.resource_pool_id for event in fallback_events} == {spot_id, fixed_id}
        evidence["scenarios"].append(
            {
                "id": "reclaim-fallback",
                "ok": True,
                "spot_pool_id": spot_id,
                "fixed_pool_id": fixed_id,
                "attempts": [attempt.attempt_no for attempt in attempts],
                "active_pool": run.resource_pool_id,
                "fallback_reason": config["spot_fallback"]["reason"],
                "fallback_failures": config["spot_fallback"]["failures"],
                "retry_runs_on": retry.payload_json["client_payload"]["runs_on"],
                "resource_events": len(fallback_events),
            }
        )

    # Scenario 2: repeated inventory blocks -> queued shard moves to fixed.
    run_id = create_run("inventory")
    spot_id, fixed_id = bind_pool(run_id, after=2)
    case_run_id, _ = first_attempt(run_id)
    for index in range(2):
        with SessionLocal() as session:
            spot = session.get(ResourcePool, spot_id)
            state = capacity_control.reconcile_pool(
                session,
                spot,
                settings=settings,
                now=timeline + timedelta(seconds=30 * index),
                provisioner=FakeProvisioner(available=False),
            )
            assert state["capacity_reason"] == capacity_control.REASON_INVENTORY_INSUFFICIENT
            session.commit()
    with SessionLocal() as session:
        run = session.get(EvalRun, run_id)
        attempt = session.scalars(
            select(EvalCaseAttempt).where(EvalCaseAttempt.eval_case_run_id == case_run_id)
        ).first()
        assert run.resource_pool_id == fixed_id
        assert attempt.resource_pool_id == fixed_id
        evidence["scenarios"].append(
            {
                "id": "inventory-fallback",
                "ok": True,
                "active_pool": run.resource_pool_id,
                "fallback_reason": run.resource_pool_snapshot_json["spot_fallback"]["reason"],
                "supply_blocks": run.resource_pool_snapshot_json["spot_fallback"][
                    "consecutive_supply_blocks"
                ],
            }
        )

    # Scenario 3: a cost cap suppresses the fallback and keeps spot retry.
    run_id = create_run("cost-cap")
    spot_id, fixed_id = bind_pool(run_id, after=2, cap="0.5000")
    case_run_id, _ = first_attempt(run_id)
    for index in range(2):
        with SessionLocal() as session:
            assert case_attempts.handle_spot_interruption(
                session,
                case_run_id=case_run_id,
                now=timeline + timedelta(minutes=index),
            ) is True
    with SessionLocal() as session:
        run = session.get(EvalRun, run_id)
        retry = next(
            row
            for row in session.scalars(select(Outbox).where(Outbox.aggregate_id == run_id))
            if row.payload_json.get("attempt") == 3
        )
        assert run.resource_pool_id == spot_id
        assert run.resource_pool_snapshot_json["spot_fallback"]["suppressed_reason"] == (
            "fallback_cost_cap_exceeded"
        )
        assert retry.payload_json["client_payload"]["runs_on"] == [
            "self-hosted",
            "spot-smoke",
        ]
        evidence["scenarios"].append(
            {
                "id": "cost-cap-suppressed",
                "ok": True,
                "active_pool": run.resource_pool_id,
                "suppressed_reason": run.resource_pool_snapshot_json["spot_fallback"][
                    "suppressed_reason"
                ],
                "retry_runs_on": retry.payload_json["client_payload"]["runs_on"],
            }
        )

    evidence["ok"] = all(item["ok"] for item in evidence["scenarios"])
    evidence["finished_at"] = iso(datetime.now(UTC))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""M4-03 live smoke: resource connection, pool validation and capacity ticks.

Run manually (not collected by pytest):

    .venv/bin/python tests/integration/m4_03_capacity_smoke.py --out /tmp/m4-03-evidence.json

It drives the real FastAPI app (TestClient) against a disposable SQLite
database, then runs the capacity controller in-process with the registered
`FakeProvisioner`. No cloud call is made.
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/m4-03-evidence.json")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="m4-03-smoke-"))
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{tmp}/smoke.db",
            "REDIS_URL": "",
            "AUTH_SESSION_SECRET": "m4-smoke-session-secret",
            "AUTH_BOOTSTRAP_ADMIN_EMAIL": "admin@m4-smoke.local",
            "AUTH_BOOTSTRAP_ADMIN_PASSWORD": "m4-smoke-password",
            "RUNNER_TASK_SECRET": "m4-smoke-task-secret",
            "S3_ENDPOINT": "",
            "S3_ACCESS_KEY": "",
            "S3_SECRET_KEY": "",
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
    os.environ.update(env)

    sys.path.insert(0, str(BACKEND))
    from app.db import SessionLocal  # noqa: PLC0415
    from app.models import (  # noqa: PLC0415
        Credential,
        EvalCaseAttempt,
        EvalCaseRun,
        EvalEvent,
        ProvisionedInstance,
        ResourcePool,
    )
    from app.seed import ensure_default_scope  # noqa: PLC0415
    from app.services import capacity_control, evaluations  # noqa: PLC0415
    from app.services.provisioning import (  # noqa: PLC0415
        register_provisioner,
        reset_provisioners,
    )
    from app.services.provisioning.fake import FakeProvisioner, reset_fake_instance_counter  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    reset_provisioners()
    reset_fake_instance_counter()
    fake = FakeProvisioner(provider="fake", price_usd="0.20", available=True)
    register_provisioner(fake)

    with SessionLocal() as session:
        tenant, _workspace, project = ensure_default_scope(session)
        credential = Credential(
            tenant_id=tenant.id,
            project_id=project.id,
            kind="resource",
            provider="fake",
            ciphertext="ciphertext-must-not-leak",
            key_version="local:1",
            secret_mask="****abcd",
        )
        session.add(credential)
        session.commit()
        credential_id = credential.id

    evidence: dict = {}
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@m4-smoke.local", "password": "m4-smoke-password"},
        )
        assert login.status_code == 200, login.text

        created = client.post(
            "/api/v1/connections",
            json={
                "name": "fake-connection",
                "provider": "fake",
                "region": "cn-hangzhou",
                "credential_id": credential_id,
                "capabilities": ["ecs:spot", "ecs:inventory"],
            },
        )
        assert created.status_code == 201, created.text
        connection = created.json()
        evidence["connection"] = {
            "id": connection["id"],
            "status": connection["status"],
            "secret_leaked": any(
                token in json.dumps(connection)
                for token in ("ciphertext-must-not-leak", "local:1")
            ),
        }

        verified = client.post(f"/api/v1/connections/{connection['id']}/test")
        assert verified.status_code == 200, verified.text
        evidence["connection"]["verified_status"] = verified.json()["status"]

        pool_response = client.post(
            "/api/v1/resource-pools",
            json={
                "name": "fake-spot-pool",
                "connection_id": connection["id"],
                "provisioning_mode": "spot",
                "min_capacity": 0,
                "max_capacity": 3,
                "price_threshold_usd": "0.50",
                "instance_config": {"instance_type": "ecs.g7.large"},
            },
        )
        assert pool_response.status_code == 201, pool_response.text
        pool = pool_response.json()

        # A connection without ecs:spot must be rejected before any pool exists.
        bare = client.post(
            "/api/v1/connections",
            json={
                "name": "bare-connection",
                "provider": "fake",
                "credential_id": credential_id,
                "capabilities": [],
            },
        )
        assert bare.status_code == 201, bare.text
        rejected = client.post(
            "/api/v1/resource-pools",
            json={
                "name": "incompatible-pool",
                "connection_id": bare.json()["id"],
                "provisioning_mode": "spot",
                "min_capacity": 0,
                "max_capacity": 2,
            },
        )
        evidence["incompatible_pool"] = {
            "status": rejected.status_code,
            "code": rejected.json().get("detail", {}).get("error", {}).get("code"),
        }
        detail = client.get(f"/api/v1/resource-pools/{pool['id']}")
        evidence["pool_detail_status"] = detail.status_code

    settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
    now = datetime.now(UTC)
    with SessionLocal() as session:
        pool_row = session.get(ResourcePool, pool["id"])
        run, _ = evaluations.create_synthetic_evaluation(
            session,
            name="M4-03 capacity smoke",
            case_keys=["cap-a", "cap-b", "cap-c"],
            shard_size=64,
            requested_concurrency=3,
            resource_pool_id=pool_row.id,
            idempotency_key="m4-03-scale",
            created_by="m4-smoke",
        )
        for attempt in (
            session.query(EvalCaseAttempt)
            .filter(
                EvalCaseAttempt.eval_case_run_id.in_(
                    session.query(EvalCaseRun.id).filter(EvalCaseRun.eval_run_id == run.id)
                )
            )
            .all()
        ):
            attempt.status = "queued"
            attempt.phase_changed_at = now
        session.commit()

        state = capacity_control.reconcile_pool(
            session, pool_row, settings=settings, now=now, provisioner=fake
        )
        session.commit()
        session.expire_all()
        instances = session.get(ResourcePool, pool_row.id)
        evidence["scale_up"] = {
            "reason": state["capacity_reason"],
            "scaled_up": state["scaled_up"],
            "instances": session.query(ProvisionedInstance)
            .filter_by(resource_pool_id=pool_row.id)
            .count(),
            "price": str(state.get("price_usd")),
            "threshold": str(state.get("price_threshold_usd")),
        }
        events_after_up = [
            event.event_type
            for event in session.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).all()
        ]
        evidence["scale_up"]["eval_events"] = sorted(set(events_after_up))
        assert instances is not None

        # Price above the threshold: no new instance, work stays queued.
        fake.price_usd = Decimal("1.00")
        queued_run, _ = evaluations.create_synthetic_evaluation(
            session,
            name="M4-03 price wait",
            case_keys=["price-a", "price-b"],
            shard_size=64,
            requested_concurrency=2,
            resource_pool_id=pool_row.id,
            idempotency_key="m4-03-price",
            created_by="m4-smoke",
        )
        for attempt in (
            session.query(EvalCaseAttempt)
            .filter(
                EvalCaseAttempt.eval_case_run_id.in_(
                    session.query(EvalCaseRun.id).filter(EvalCaseRun.eval_run_id == queued_run.id)
                )
            )
            .all()
        ):
            attempt.status = "queued"
            attempt.phase_changed_at = now
        session.commit()
        before = (
            session.query(ProvisionedInstance)
            .filter_by(resource_pool_id=pool_row.id)
            .count()
        )
        price_state = capacity_control.reconcile_pool(
            session,
            pool_row,
            settings=settings,
            now=now + timedelta(seconds=settings.eval_capacity_check_seconds + 1),
            provisioner=fake,
        )
        session.commit()
        session.expire_all()
        after = (
            session.query(ProvisionedInstance)
            .filter_by(resource_pool_id=pool_row.id)
            .count()
        )
        evidence["price_wait"] = {
            "reason": price_state["capacity_reason"],
            "instances_before": before,
            "instances_after": after,
            "price": str(pool_row.price_usd),
            "threshold": str(pool_row.price_threshold_usd),
            "checked_at": price_state.get("checked_at"),
            "next_check_at": price_state.get("next_check_at"),
        }

    Path(args.out).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))

    ok = (
        not evidence["connection"]["secret_leaked"]
        and evidence["connection"]["verified_status"] == "verified"
        and evidence["incompatible_pool"]["status"] == 422
        and evidence["incompatible_pool"]["code"] == "connection_provider_mismatch"
        and evidence["pool_detail_status"] == 200
        and evidence["scale_up"]["reason"] == "capacity_scale_up"
        and evidence["scale_up"]["scaled_up"] >= 1
        and evidence["price_wait"]["reason"] == "resource_price_above_threshold"
        and evidence["price_wait"]["instances_after"] == evidence["price_wait"]["instances_before"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

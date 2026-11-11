"""M5-19: pool utilization, today's resource cost and queue risk.

The console shows these numbers on the pool card. They must be recomputable
from persisted rows: utilization is busy runners / pool ceiling, and today's
cost is each instance's CNY/hour snapshot times the hours it overlapped the
local calendar day. These tests freeze "now" so the day window is stable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.models import ProvisionedInstance, ResourcePool, Runner
from app.seed import ensure_default_scope
from app.services import resource_usage
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

FIXED_NOW = datetime(2026, 11, 11, 14, 0, tzinfo=UTC)  # 22:00 Asia/Shanghai
WINDOW_START = datetime(2026, 11, 10, 16, 0, tzinfo=UTC)  # 00:00 Asia/Shanghai


@pytest.fixture
def scope(session: Session) -> dict[str, str]:
    tenant, _workspace, project = ensure_default_scope(session)
    return {"tenant_id": tenant.id, "project_id": project.id}


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Freeze ``resource_usage``' clock without touching other modules."""

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None):  # type: ignore[override]
            return FIXED_NOW if tz is None else FIXED_NOW.astimezone(tz)

    monkeypatch.setattr(resource_usage, "datetime", FrozenDateTime)
    return FIXED_NOW


def _pool(session: Session, scope: dict[str, str], **overrides: object) -> ResourcePool:
    values: dict[str, object] = {
        "tenant_id": scope["tenant_id"],
        "project_id": scope["project_id"],
        "name": f"pool-{uuid4().hex[:8]}",
        "provider_key": "fake",
        "provisioning_mode": "spot",
        "min_capacity": 0,
        "max_capacity": 4,
        "instance_config_json": {"runners_per_instance": 1},
        "labels_json": {},
        "price_cny": Decimal("3.6000"),
        "status": "active",
    }
    values.update(overrides)
    pool = ResourcePool(**values)
    session.add(pool)
    session.commit()
    return pool


def _runner(
    session: Session,
    pool: ResourcePool,
    status: str,
    *,
    name: str | None = None,
) -> Runner:
    runner = Runner(
        tenant_id=pool.tenant_id,
        resource_pool_id=pool.id,
        provider_runner_id=name or f"runner-{uuid4().hex[:8]}",
        name=name or "runner",
        labels_json=["self-hosted"],
        status=status,
        last_heartbeat_at=FIXED_NOW - timedelta(seconds=5),
        registered_at=FIXED_NOW - timedelta(minutes=5),
    )
    session.add(runner)
    session.commit()
    return runner


def _instance(
    session: Session,
    pool: ResourcePool,
    *,
    requested_at: datetime,
    released_at: datetime | None,
    price: str | None,
    status: str = "released",
) -> ProvisionedInstance:
    cost_json: dict[str, object] = {"requested_at": requested_at.isoformat()}
    if price is not None:
        cost_json["price_cny"] = price
    instance = ProvisionedInstance(
        tenant_id=pool.tenant_id,
        resource_pool_id=pool.id,
        provider_instance_id=f"fake-{uuid4().hex[:8]}",
        status=status,
        cost_json=cost_json,
        created_at=requested_at,
        released_at=released_at,
    )
    session.add(instance)
    session.commit()
    return instance


def test_day_window_uses_configured_offset() -> None:
    start, end = resource_usage.day_window(FIXED_NOW, 8)

    assert start == WINDOW_START
    assert end == FIXED_NOW


def test_queue_risk_triggers_at_capacity_with_waiting_jobs() -> None:
    at_capacity = resource_usage.queue_risk({"online": 3, "busy": 1}, 4, 1)
    assert at_capacity["at_risk"] is True
    assert at_capacity["near_capacity"] is True
    assert at_capacity["threshold"] == resource_usage.QUEUE_ALERT_THRESHOLD

    # A long queue is a warning even when the pool still has spare runners.
    long_queue = resource_usage.queue_risk({}, 50, 6)
    assert long_queue["at_risk"] is True
    assert long_queue["near_capacity"] is False

    idle = resource_usage.queue_risk({"online": 1, "busy": 0}, 4, 0)
    assert idle["at_risk"] is False


def test_queue_risk_ignores_empty_queue_below_threshold() -> None:
    calm = resource_usage.queue_risk({"online": 3, "busy": 1}, 4, 0)
    assert calm["at_risk"] is False


def test_pool_list_exposes_utilization_and_today_cost(
    client: TestClient,
    session: Session,
    scope: dict[str, str],
    frozen_now: datetime,
) -> None:
    pool = _pool(session, scope, max_capacity=4)
    _runner(session, pool, "busy", name="busy-1")
    _runner(session, pool, "online", name="online-1")
    _runner(session, pool, "offline", name="offline-1")
    # 12:00 → 14:00 UTC is two hours inside the Shanghai day; 3 CNY/hour.
    _instance(
        session,
        pool,
        requested_at=datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
        released_at=datetime(2026, 11, 11, 14, 0, tzinfo=UTC),
        price="3.0000",
    )

    response = client.get("/api/v1/resource-pools")
    assert response.status_code == 200, response.text
    usage = response.json()["items"][0]["usage"]

    assert usage["utilization"] == {
        "ratio": 0.25,
        "busy_runners": 1,
        "live_runners": 2,
        "capacity": 4,
    }
    assert usage["cost"]["today_cny"] == "6.000000"
    assert usage["cost"]["spot_cny"] == "6.000000"
    assert usage["cost"]["fixed_cny"] == "0.000000"
    assert usage["cost"]["instance_count"] == 1
    assert usage["cost"]["unpriced_instance_count"] == 0
    assert usage["cost"]["window_start"] == WINDOW_START.isoformat(timespec="microseconds")
    assert usage["cost"]["window_end"] == FIXED_NOW.isoformat(timespec="microseconds")
    assert usage["queue_risk"]["at_risk"] is False


def test_pool_detail_includes_usage_and_released_instance_is_prorated(
    client: TestClient,
    session: Session,
    scope: dict[str, str],
    frozen_now: datetime,
) -> None:
    pool = _pool(session, scope, max_capacity=8)
    _instance(
        session,
        pool,
        requested_at=datetime(2026, 11, 11, 13, 0, tzinfo=UTC),
        released_at=None,
        price="2.0000",
        status="online",
    )

    detail = client.get(f"/api/v1/resource-pools/{pool.id}").json()
    usage = detail["usage"]
    assert usage["cost"]["today_cny"] == "2.000000"
    assert usage["cost"]["instance_count"] == 1
    assert detail["runners"]["total"] == 0


def test_unpriced_instance_is_reported_without_inventing_a_cost(
    client: TestClient,
    session: Session,
    scope: dict[str, str],
    frozen_now: datetime,
) -> None:
    pool = _pool(session, scope, price_cny=None)
    _instance(
        session,
        pool,
        requested_at=datetime(2026, 11, 11, 13, 0, tzinfo=UTC),
        released_at=None,
        price=None,
    )

    usage = client.get("/api/v1/resource-pools").json()["items"][0]["usage"]
    assert usage["cost"]["today_cny"] == "0.000000"
    assert usage["cost"]["instance_count"] == 0
    assert usage["cost"]["unpriced_instance_count"] == 1

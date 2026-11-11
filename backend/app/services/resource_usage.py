"""Pool utilization, daily resource cost and queue risk (M5-19).

The resource-pool console shows two numbers the operator can recompute from
persisted rows instead of trusting the front end:

* **utilization** — ``busy runners / pool max_capacity``.  A runner is busy
  while it executes one job (one runner, one job), so the ratio is the share of
  the pool's concurrency ceiling currently doing work.
* **today's resource cost** — for every provisioned instance, the CNY/hour
  price snapshot captured when the instance was created times the hours the
  instance overlapped today's local window.  Fixed (self-hosted) runners have
  no cloud price and therefore contribute zero.

The day window follows ``RESOURCE_COST_UTC_OFFSET_HOURS`` (Asia/Shanghai by
default) so "今日" matches the operator's calendar rather than UTC.  Both ends
of the window are part of the payload so the number is auditable.

The queue-risk flag is also computed here: a pool with waiting jobs at or near
its live-runner ceiling is a capacity warning, and the console renders the same
boolean the API returns instead of re-deriving it in the browser.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import ProvisionedInstance, ResourcePool
from ..money import Money, money_str

#: Waiting jobs above this count always raise the warning, even when the pool
#: still has spare runners (for example because the dispatcher is behind).
QUEUE_ALERT_THRESHOLD = 5
#: Live runners at or above this share of the pool ceiling count as "near
#: capacity"; together with a non-empty queue that is the queue-risk scenario.
UTILIZATION_ALERT_RATIO = Decimal("0.8")
_HOUR = Decimal(3600)
#: Instance states that prove the cloud instance exists (``requested`` means
#: the provider accepted the create call, even if it is still booting).
_BILLABLE_INSTANCE_STATUSES = {
    "requested",
    "creating",
    "registering",
    "online",
    "draining",
    "released",
    "interrupted",
}
_INSTANCE_TIME_FIELDS = ("requested_at", "created_at")


def _aware(value: datetime | None) -> datetime | None:
    """Treat SQLite's naive timestamps as UTC, matching the rest of the API."""

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return None if aware is None else aware.isoformat(timespec="microseconds")


def _parse_instance_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def day_window(now: datetime, offset_hours: int) -> tuple[datetime, datetime]:
    """Local calendar day containing ``now`` as a UTC ``[start, end]`` pair."""

    zone = timezone(timedelta(hours=offset_hours))
    local = now.astimezone(zone)
    start = datetime.combine(local.date(), time.min, tzinfo=zone)
    return start.astimezone(UTC), now.astimezone(UTC)


def utilization(runners: Mapping[str, int], capacity: int) -> dict[str, Any]:
    """Share of the pool ceiling that is executing a job right now."""

    safe_capacity = max(int(capacity or 0), 0)
    busy = int(runners.get("busy") or 0)
    live = int(runners.get("online") or 0) + busy
    ratio = (Decimal(busy) / Decimal(safe_capacity)) if safe_capacity else Decimal(0)
    return {
        "ratio": float(ratio.quantize(Decimal("0.0001"))),
        "busy_runners": busy,
        "live_runners": live,
        "capacity": safe_capacity,
    }


def queue_risk(
    runners: Mapping[str, int],
    capacity: int,
    queued_attempts: int,
) -> dict[str, Any]:
    """Whether the pool is at risk of starving queued jobs."""

    safe_capacity = max(int(capacity or 0), 0)
    queued = max(int(queued_attempts or 0), 0)
    live = int(runners.get("online") or 0) + int(runners.get("busy") or 0)
    live_ratio = (Decimal(live) / Decimal(safe_capacity)) if safe_capacity else Decimal(0)
    near_capacity = safe_capacity > 0 and live_ratio >= UTILIZATION_ALERT_RATIO
    at_risk = queued > 0 and (near_capacity or queued > QUEUE_ALERT_THRESHOLD)
    return {
        "at_risk": at_risk,
        "queued_attempts": queued,
        "threshold": QUEUE_ALERT_THRESHOLD,
        "near_capacity": near_capacity,
        "live_runners": live,
        "capacity": safe_capacity,
        "live_ratio": float(live_ratio.quantize(Decimal("0.0001"))),
    }


def _instance_price(
    instance: ProvisionedInstance,
    pool: ResourcePool,
    quote_price: str | None,
) -> Money | None:
    """CNY/hour captured on the instance, else the pool's current quote."""

    cost = instance.cost_json if isinstance(instance.cost_json, dict) else {}
    recorded = cost.get("price_cny")
    if isinstance(recorded, str) and recorded:
        try:
            return Money.of(recorded)
        except ValueError:
            pass
    for fallback in (quote_price, money_str(pool.price_cny)):
        if isinstance(fallback, str) and fallback:
            try:
                return Money.of(fallback)
            except ValueError:
                continue
    return None


def _instance_start(instance: ProvisionedInstance) -> datetime | None:
    cost = instance.cost_json if isinstance(instance.cost_json, dict) else {}
    for field in _INSTANCE_TIME_FIELDS:
        if field == "requested_at":
            parsed = _parse_instance_time(cost.get(field))
        else:
            parsed = _aware(instance.created_at)
        if parsed is not None:
            return parsed
    return None


def _overlap_seconds(
    instance: ProvisionedInstance, window_start: datetime, window_end: datetime
) -> int:
    start = _instance_start(instance)
    if start is None:
        return 0
    end = _aware(instance.released_at) or window_end
    start = max(start, window_start)
    end = min(end, window_end)
    if end <= start:
        return 0
    return int((end - start).total_seconds())


def pool_today_cost(
    session: Session,
    pool: ResourcePool,
    *,
    now: datetime,
    offset_hours: int,
    quote_price: str | None = None,
) -> dict[str, Any]:
    """Today's cloud cost for one pool, recomputable from instance rows."""

    window_start, window_end = day_window(now, offset_hours)
    instances = list(
        session.scalars(
            select(ProvisionedInstance)
            .where(ProvisionedInstance.resource_pool_id == pool.id)
            .order_by(ProvisionedInstance.created_at)
        )
    )
    spot = Money.zero()
    fixed = Money.zero()
    counted = 0
    unpriced = 0
    for instance in instances:
        if instance.status not in _BILLABLE_INSTANCE_STATUSES:
            continue
        seconds = _overlap_seconds(instance, window_start, window_end)
        if seconds <= 0:
            continue
        price = _instance_price(instance, pool, quote_price)
        if price is None:
            unpriced += 1
            continue
        amount = price * seconds / _HOUR
        if pool.provisioning_mode == "spot":
            spot = spot + amount
        else:
            fixed = fixed + amount
        counted += 1
    return {
        "today_cny": money_str((spot + fixed).quantize()),
        "spot_cny": money_str(spot.quantize()),
        "fixed_cny": money_str(fixed.quantize()),
        "instance_count": counted,
        "unpriced_instance_count": unpriced,
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "basis": "实例运行时长 × 创建时记录的 CNY/小时 价格快照",
    }


def pool_usage(
    session: Session,
    pool: ResourcePool,
    *,
    runners: Mapping[str, Any],
    capacity_state: Mapping[str, Any],
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One payload holding utilization, cost and queue risk for a pool card."""

    now = now or datetime.now(UTC)
    quote_price = (
        capacity_state.get("price_cny")
        if isinstance(capacity_state.get("price_cny"), str)
        else None
    )
    return {
        "utilization": utilization(runners, pool.max_capacity),
        "cost": pool_today_cost(
            session,
            pool,
            now=now,
            offset_hours=settings.resource_cost_utc_offset_hours,
            quote_price=quote_price,
        ),
        "queue_risk": queue_risk(
            runners,
            pool.max_capacity,
            int(capacity_state.get("queued_attempts") or 0),
        ),
    }

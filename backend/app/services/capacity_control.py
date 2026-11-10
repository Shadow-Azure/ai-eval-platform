"""Spot resource-pool capacity controller (M4-03 B, design 06 §6.1).

The controller keeps a pool's capacity in step with its queue:

    gap      = queued shards - idle runners - starting instances * runners/instance
    current  = online/busy runners + starting instances
    desired  = min(max_capacity, current + ceil(gap / runners/instance))

A tick either creates a bounded batch of instances through the provisioner
seam, releases idle instances above ``min_capacity`` or only records a waiting
reason. Two rules are load-bearing for the product:

1. **Waiting never fails work.** A price above threshold, missing inventory or
   a provider error only persist a stable reason and an event; evaluation
   cases stay queued and the retry loop keeps trying.
2. **Drain before release.** Scale-down marks the runner ``draining``, then
   deregisters it (``offline``), and only then releases its instance, so a
   released instance can never take a newly dispatched job with it.

Everything is idempotent across ticks: state events use a dedupe key derived
from the pool and the observable state, so repeated passes over an unchanged
pool add no rows.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    Connection,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    ProvisionedInstance,
    ResourceEvent,
    ResourcePool,
    Runner,
)
from .provisioning import (
    InstanceSpec,
    PriceQuote,
    Provisioner,
    ProvisionerError,
    get_provisioner,
)

#: Reasons persisted on ``resource_pool.capacity_reason`` (design 06 §6.1).
REASON_PRICE_ABOVE_THRESHOLD = "resource_price_above_threshold"
REASON_INVENTORY_INSUFFICIENT = "resource_inventory_insufficient"
REASON_PROVISIONER_UNAVAILABLE = "provisioner_unavailable"
REASON_CAPACITY_SCALE_UP = "capacity_scale_up"
REASON_CAPACITY_SCALE_DOWN = "capacity_scale_down"
REASON_PROVISIONING_FAILED = "resource_provisioning_failed"
REASON_PROVIDER_AUTH_FAILED = "resource_provider_auth_failed"
REASON_ACCOUNT_BALANCE_INSUFFICIENT = "resource_account_balance_insufficient"
REASON_RUNNER_REGISTRATION_UNAVAILABLE = "runner_registration_unavailable"

EVENT_CAPACITY_UPDATED = "resource.capacity.updated"
EVENT_CAPACITY_SCALED_DOWN = "resource.capacity.scaled_down"
EVENT_CAPACITY_RELEASED = "resource.capacity.released"
EVENT_PROVISIONING_FAILED = "resource.provisioning_failed"
EVENT_RELEASE_FAILED = "resource.release_failed"
EVENT_PROVISIONING_TESTED = "resource.provisioning_tested"
EVENT_INSTANCE_REGISTERED = "resource.instance.registered"
EVENT_INSTANCE_INTERRUPTED = "resource.instance.interrupted"
EVENT_INSTANCE_CREATE_TIMEOUT = "resource.instance.create_timeout"
EVENT_PROVIDER_SYNC_FAILED = "resource.provider.sync_failed"

PROVISIONING_MODE_SPOT = "spot"
PROVISIONING_MODE_FIXED = "fixed"

#: Shards in this attempt state still wait for a runner.
QUEUED_ATTEMPT_STATUS = "queued"
#: Provisioned instances that already reserve future runner capacity.
STARTING_INSTANCE_STATUSES = ("requested", "creating", "registering")
#: Runners that count towards the pool's current size.
LIVE_RUNNER_STATUSES = ("online", "busy")
#: Runner state that can be released by scale-down.
IDLE_RUNNER_STATUS = "online"
DEFAULT_RUNNERS_PER_INSTANCE = 1
DEFAULT_INSTANCE_TYPE = "standard"


# ---------------------------------------------------------------------------
# Small normalization helpers (SQLite returns naive datetimes)
# ---------------------------------------------------------------------------


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return None if aware is None else aware.isoformat(timespec="microseconds")


def _decimal_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _json_safe(value: Any) -> Any:
    """Event payloads must stay JSON-serializable on both dialects."""

    if isinstance(value, Decimal):
        return _decimal_str(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return value


def _instance_config(pool: ResourcePool) -> dict[str, Any]:
    return dict(pool.instance_config_json) if isinstance(pool.instance_config_json, dict) else {}


def runners_per_instance(pool: ResourcePool) -> int:
    """Runner slots one provisioned instance contributes (default 1)."""

    raw = _instance_config(pool).get("runners_per_instance", DEFAULT_RUNNERS_PER_INSTANCE)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RUNNERS_PER_INSTANCE
    return max(1, value)


def instance_spec(pool: ResourcePool) -> InstanceSpec:
    """Provisioner request built from the pool's frozen instance config."""

    config = _instance_config(pool)
    instance_type = config.get("instance_type")
    if not isinstance(instance_type, str) or not instance_type.strip():
        instance_type = DEFAULT_INSTANCE_TYPE
    image_id = config.get("image_id")
    return InstanceSpec(
        instance_type=instance_type,
        image_id=image_id if isinstance(image_id, str) and image_id else None,
        spot=pool.provisioning_mode == PROVISIONING_MODE_SPOT,
    )


# ---------------------------------------------------------------------------
# State queries
# ---------------------------------------------------------------------------


def _queued_attempts(session: Session, pool: ResourcePool) -> int:
    """Shards of this pool's runs that are still waiting for a runner."""

    statement = (
        select(func.count())
        .select_from(EvalCaseAttempt)
        .join(
            EvalCaseRun,
            and_(
                EvalCaseRun.tenant_id == EvalCaseAttempt.tenant_id,
                EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id,
            ),
        )
        .join(
            EvalRun,
            and_(
                EvalRun.tenant_id == EvalCaseRun.tenant_id,
                EvalRun.id == EvalCaseRun.eval_run_id,
            ),
        )
        .where(
            EvalRun.resource_pool_id == pool.id,
            EvalCaseAttempt.status == QUEUED_ATTEMPT_STATUS,
        )
    )
    return int(session.scalar(statement) or 0)


def _runner_count(
    session: Session,
    pool: ResourcePool,
    *,
    settings: Settings,
    now: datetime,
    statuses: tuple[str, ...],
) -> int:
    stale_before = now - timedelta(seconds=settings.runner_heartbeat_timeout_seconds)
    statement = (
        select(func.count())
        .select_from(Runner)
        .where(
            Runner.resource_pool_id == pool.id,
            Runner.status.in_(statuses),
            Runner.last_heartbeat_at.is_(None) | (Runner.last_heartbeat_at >= stale_before),
        )
    )
    return int(session.scalar(statement) or 0)


def _starting_instances(session: Session, pool: ResourcePool) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ProvisionedInstance)
            .where(
                ProvisionedInstance.resource_pool_id == pool.id,
                ProvisionedInstance.status.in_(STARTING_INSTANCE_STATUSES),
            )
        )
        or 0
    )


def _connection(session: Session, pool: ResourcePool) -> Connection | None:
    if not pool.connection_id:
        return None
    return session.get(Connection, pool.connection_id)


def _quote(
    pool: ResourcePool,
    *,
    session: Session,
    provisioner: Provisioner | None,
    connection: Connection | None,
    now: datetime,
) -> PriceQuote | None:
    """Spot quote, or ``None`` when no provisioner/connection can answer."""

    if provisioner is None:
        return None
    if pool.provisioning_mode == PROVISIONING_MODE_SPOT and connection is None:
        return None
    return provisioner.quote(
        connection=connection, pool=pool, now=now, session=session
    )


def _capacity_reason(
    pool: ResourcePool,
    *,
    gap: int,
    idle: int,
    quote: PriceQuote | None,
    provisioner: Provisioner | None,
    connection: Connection | None,
) -> str | None:
    """Stable reason for the state, ordered by what blocks creation first."""

    if gap > 0:
        threshold = pool.price_threshold_usd
        if quote is not None and threshold is not None:
            # Provisioners must quote Decimals, but coerce defensively so a
            # provider returning a str/float cannot turn into a swallowed error.
            if Decimal(str(quote.price_usd)) > Decimal(str(threshold)):
                return REASON_PRICE_ABOVE_THRESHOLD
        if quote is not None and not quote.available:
            return REASON_INVENTORY_INSUFFICIENT
        if pool.provisioning_mode == PROVISIONING_MODE_SPOT and (
            provisioner is None or connection is None
        ):
            return REASON_PROVISIONER_UNAVAILABLE
        return REASON_CAPACITY_SCALE_UP
    if gap == 0 and idle > pool.min_capacity:
        return REASON_CAPACITY_SCALE_DOWN
    return None


def _build_state(
    session: Session,
    pool: ResourcePool,
    *,
    settings: Settings,
    now: datetime,
    provisioner: Provisioner | None,
    connection: Connection | None,
    quote: PriceQuote | None,
    quote_error: ProvisionerError | None = None,
) -> dict[str, Any]:
    queued = _queued_attempts(session, pool)
    idle = _runner_count(
        session,
        pool,
        settings=settings,
        now=now,
        statuses=(IDLE_RUNNER_STATUS,),
    )
    live = _runner_count(
        session,
        pool,
        settings=settings,
        now=now,
        statuses=LIVE_RUNNER_STATUSES,
    )
    starting = _starting_instances(session, pool)
    slots = runners_per_instance(pool)
    gap = max(0, queued - idle - starting * slots)
    current_size = live + starting
    desired = min(pool.max_capacity, current_size + math.ceil(gap / slots))
    reason = _capacity_reason(
        pool,
        gap=gap,
        idle=idle,
        quote=quote,
        provisioner=provisioner,
        connection=connection,
    )
    if quote_error is not None:
        reason = _provisioning_reason(quote_error.code)
    price = quote.price_usd if quote is not None else None
    return {
        "pool_id": pool.id,
        "provisioning_mode": pool.provisioning_mode,
        "checked_at": _iso(now),
        "queued_attempts": queued,
        "idle_runners": idle,
        "live_runners": live,
        "starting_instances": starting,
        "runners_per_instance": slots,
        "min_capacity": pool.min_capacity,
        "max_capacity": pool.max_capacity,
        "current_size": current_size,
        "desired_capacity": desired,
        "capacity_gap": gap,
        "price_usd": _decimal_str(price),
        "price_threshold_usd": _decimal_str(pool.price_threshold_usd),
        "price_available": quote.available if quote is not None else None,
        "provider_error": quote_error.code if quote_error is not None else None,
        "capacity_reason": reason,
        "last_checked_at": _iso(pool.last_checked_at),
        "next_check_at": _iso(pool.next_check_at),
    }


def pool_capacity_state(
    session: Session,
    pool: ResourcePool,
    *,
    settings: Settings,
    now: datetime | None = None,
    provisioner: Provisioner | None = None,
) -> dict[str, Any]:
    """Read-only capacity snapshot for ``pool`` (no writes, no events)."""

    now = now or datetime.now(UTC)
    connection = _connection(session, pool)
    quote_error: ProvisionerError | None = None
    try:
        quote = _quote(
            pool, session=session, provisioner=provisioner, connection=connection, now=now
        )
    except ProvisionerError as exc:
        quote = None
        quote_error = exc
    return _build_state(
        session,
        pool,
        settings=settings,
        now=now,
        provisioner=provisioner,
        connection=connection,
        quote=quote,
        quote_error=quote_error,
    )


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


def append_resource_event(
    session: Session,
    pool: ResourcePool,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    dedupe_key: str | None = None,
) -> ResourceEvent | None:
    """Add one event unless ``dedupe_key`` was already recorded for the pool.

    The unique ``(tenant_id, dedupe_key)`` constraint is the durable dedupe:
    it survives process restarts and commits per pool, so an unchanged pool
    state never adds a second row on the next tick.
    """

    if dedupe_key is not None:
        existing = session.scalar(
            select(ResourceEvent.id).where(
                ResourceEvent.tenant_id == pool.tenant_id,
                ResourceEvent.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            return None
    event = ResourceEvent(
        tenant_id=pool.tenant_id,
        resource_pool_id=pool.id,
        connection_id=pool.connection_id,
        event_type=event_type,
        payload_json=_json_safe(dict(payload or {})),
        dedupe_key=dedupe_key,
    )
    session.add(event)
    session.flush()
    return event


def _state_dedupe_key(pool: ResourcePool, state: Mapping[str, Any], reason: str | None) -> str:
    return (
        f"{pool.id}:capacity:{reason}:{state['capacity_gap']}:{state['price_usd']}"
    )


def _provisioning_reason(code: str) -> str:
    """Map a provider error code to a stable user-facing pool reason."""

    if code == "runner_registration_unavailable":
        return REASON_RUNNER_REGISTRATION_UNAVAILABLE
    if code == "cloud_account_balance_insufficient":
        return REASON_ACCOUNT_BALANCE_INSUFFICIENT
    if code in ("aliyun_auth_failed", "connection_credential_invalid",
                "connection_credential_unreadable"):
        return REASON_PROVIDER_AUTH_FAILED
    return REASON_PROVISIONING_FAILED


# ---------------------------------------------------------------------------
# Scale up / scale down
# ---------------------------------------------------------------------------


def _record_provisioning_failure(
    session: Session,
    pool: ResourcePool,
    *,
    code: str,
    state: Mapping[str, Any],
    message: str | None = None,
) -> None:
    append_resource_event(
        session,
        pool,
        EVENT_PROVISIONING_FAILED,
        {
            "reason": pool.capacity_reason,
            "code": code,
            "message": message,
            "capacity_gap": state["capacity_gap"],
            "price_usd": state["price_usd"],
        },
        dedupe_key=(
            f"{pool.id}:provisioning_failed:{code}:"
            f"{state['capacity_gap']}:{state['price_usd']}"
        ),
    )


def _scale_up(
    session: Session,
    pool: ResourcePool,
    *,
    state: Mapping[str, Any],
    provisioner: Provisioner,
    connection: Connection | None,
    settings: Settings,
    now: datetime,
) -> int:
    slots = int(state["runners_per_instance"])
    headroom = max(0, pool.max_capacity - int(state["current_size"]))
    needed = math.ceil(int(state["capacity_gap"]) / slots) if state["capacity_gap"] else 0
    batch = min(headroom, needed, max(0, int(settings.eval_capacity_batch_size)))
    if batch <= 0:
        return 0

    spec = instance_spec(pool)
    created = 0
    for _ in range(batch):
        try:
            provisioner.create(
                session=session,
                connection=connection,
                pool=pool,
                spec=spec,
                now=now,
            )
        except ProvisionerError as exc:
            # Provider failures never fail cases: record the reason and let the
            # next tick (after next_check_at) try again.
            pool.capacity_reason = _provisioning_reason(exc.code)
            _record_provisioning_failure(
                session, pool, code=exc.code, state=state, message=exc.message
            )
            break
        created += 1
    return created


def _runner_instance(
    session: Session, pool: ResourcePool, runner: Runner
) -> ProvisionedInstance | None:
    return session.scalar(
        select(ProvisionedInstance)
        .where(
            ProvisionedInstance.resource_pool_id == pool.id,
            ProvisionedInstance.runner_id == runner.id,
            ProvisionedInstance.status != "released",
        )
        .order_by(ProvisionedInstance.created_at.desc(), ProvisionedInstance.id.desc())
    )


def _idle_runners_for_release(
    session: Session,
    pool: ResourcePool,
    *,
    settings: Settings,
    now: datetime,
    limit: int,
) -> list[Runner]:
    """Fresh idle runners, newest first, so the newest instance is retired."""

    stale_before = now - timedelta(seconds=settings.runner_heartbeat_timeout_seconds)
    statement = (
        select(Runner)
        .where(
            Runner.resource_pool_id == pool.id,
            Runner.status == IDLE_RUNNER_STATUS,
            Runner.last_heartbeat_at.is_(None) | (Runner.last_heartbeat_at >= stale_before),
        )
        .order_by(
            func.coalesce(Runner.registered_at, Runner.created_at).desc(),
            Runner.id.desc(),
        )
        .limit(limit)
    )
    return list(session.scalars(statement))


def _retry_pending_releases(
    session: Session,
    pool: ResourcePool,
    *,
    provisioner: Provisioner,
    now: datetime,
) -> int:
    """Retry instances whose release failed in an earlier tick (M4-03).

    The runner is already deregistered at that point, so it is no longer in
    ``_idle_runners_for_release``; without this pass a failed release would
    keep paying for the instance forever.
    """

    released = 0
    pending = list(
        session.scalars(
            select(ProvisionedInstance).where(
                ProvisionedInstance.resource_pool_id == pool.id,
                ProvisionedInstance.status == "draining",
            )
        )
    )
    for instance in pending:
        try:
            provisioner.release(session=session, instance=instance, now=now)
        except ProvisionerError as exc:
            append_resource_event(
                session,
                pool,
                EVENT_RELEASE_FAILED,
                {
                    "instance_id": instance.id,
                    "provider_instance_id": instance.provider_instance_id,
                    "code": exc.code,
                    "message": exc.message,
                    "retried": True,
                },
                dedupe_key=f"{pool.id}:release_failed:{instance.id}:{exc.code}",
            )
            continue
        instance.status = "released"
        instance.released_at = now
        append_resource_event(
            session,
            pool,
            EVENT_CAPACITY_RELEASED,
            {
                "instance_id": instance.id,
                "provider_instance_id": instance.provider_instance_id,
                "retried": True,
            },
            dedupe_key=f"{pool.id}:released:{instance.id}",
        )
        released += 1
    return released


def _scale_down(
    session: Session,
    pool: ResourcePool,
    *,
    state: Mapping[str, Any],
    provisioner: Provisioner,
    settings: Settings,
    now: datetime,
) -> int:
    excess = int(state["idle_runners"]) - pool.min_capacity
    if excess <= 0:
        return 0

    retired = 0
    for order, runner in enumerate(
        _idle_runners_for_release(
            session, pool, settings=settings, now=now, limit=excess
        ),
        start=1,
    ):
        # Drain first: never release an instance whose runner can still pick up
        # a job. The intermediate flush makes the ordering observable.
        runner.status = "draining"
        session.flush()
        runner.status = "offline"
        runner.deregistered_at = now
        session.flush()

        instance = _runner_instance(session, pool, runner)
        if instance is not None:
            try:
                provisioner.release(session=session, instance=instance, now=now)
            except ProvisionerError as exc:
                # Keep the instance for the next tick's retry pass; the runner is
                # already deregistered so it cannot accept new work. Do not count
                # this as a successful scale-down (the instance still costs).
                instance.status = "draining"
                session.flush()
                append_resource_event(
                    session,
                    pool,
                    EVENT_RELEASE_FAILED,
                    {
                        "runner_id": runner.id,
                        "instance_id": instance.id,
                        "provider_instance_id": instance.provider_instance_id,
                        "code": exc.code,
                        "message": exc.message,
                        "order": order,
                    },
                    dedupe_key=f"{pool.id}:release_failed:{instance.id}:{exc.code}",
                )
                continue
            instance.status = "released"
            instance.released_at = now
        append_resource_event(
            session,
            pool,
            EVENT_CAPACITY_SCALED_DOWN,
            {
                "runner_id": runner.id,
                "instance_id": instance.id if instance is not None else None,
                "provider_instance_id": (
                    instance.provider_instance_id if instance is not None else None
                ),
                "order": order,
                "released": True,
                "price_usd": state["price_usd"],
            },
            dedupe_key=f"{pool.id}:scaled_down:{runner.id}",
        )
        retired += 1
    return retired


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconcile_pool(
    session: Session,
    pool: ResourcePool,
    *,
    settings: Settings,
    now: datetime | None = None,
    provisioner: Provisioner | None = None,
) -> dict[str, Any]:
    """One capacity pass over ``pool``; never raises for provider failures.

    Persists the price/capacity snapshot and ``last_checked_at`` /
    ``next_check_at``, then scales up or down when the state calls for it.
    Returns the state snapshot plus ``scaled_up``/``scaled_down`` counters.
    The caller owns the transaction (the worker commits per pool).
    """

    now = now or datetime.now(UTC)
    connection = _connection(session, pool)
    synced: dict[str, int] = {}
    if provisioner is not None and connection is not None:
        _retry_pending_releases(session, pool, provisioner=provisioner, now=now)
        try:
            synced = dict(
                provisioner.sync(
                    session=session, connection=connection, pool=pool, now=now
                )
            )
        except ProvisionerError:
            # The provider already recorded a deduped sync-failure event; the
            # tick continues with the last known local state so one bad cloud
            # call cannot stall price/scale-down bookkeeping.
            synced = {}
    quote_error: ProvisionerError | None = None
    try:
        quote = _quote(
            pool, session=session, provisioner=provisioner, connection=connection, now=now
        )
    except ProvisionerError as exc:
        # A broken credential/API must not crash the worker tick: record the
        # stable reason, keep cases queued and retry on the next interval.
        quote = None
        quote_error = exc
    state = _build_state(
        session,
        pool,
        settings=settings,
        now=now,
        provisioner=provisioner,
        connection=connection,
        quote=quote,
        quote_error=quote_error,
    )

    reason = state["capacity_reason"]
    pool.price_usd = quote.price_usd if quote is not None else None
    pool.capacity_reason = reason
    pool.last_checked_at = now
    pool.next_check_at = now + timedelta(seconds=settings.eval_capacity_check_seconds)
    if quote_error is not None:
        _record_provisioning_failure(
            session,
            pool,
            code=quote_error.code,
            state=state,
            message=quote_error.message,
        )

    scaled_up = 0
    scaled_down = 0
    can_provision = (
        pool.provisioning_mode == PROVISIONING_MODE_SPOT
        and provisioner is not None
        and connection is not None
    )
    if can_provision:
        if reason == REASON_CAPACITY_SCALE_UP:
            scaled_up = _scale_up(
                session,
                pool,
                state=state,
                provisioner=provisioner,
                connection=connection,
                settings=settings,
                now=now,
            )
        elif reason == REASON_CAPACITY_SCALE_DOWN:
            scaled_down = _scale_down(
                session,
                pool,
                state=state,
                provisioner=provisioner,
                settings=settings,
                now=now,
            )
        elif reason == REASON_INVENTORY_INSUFFICIENT:
            _record_provisioning_failure(
                session,
                pool,
                code="inventory_unavailable",
                state=state,
                message="provider reports no inventory for the requested instance type",
            )

    reported_reason = pool.capacity_reason
    append_resource_event(
        session,
        pool,
        EVENT_CAPACITY_UPDATED,
        {
            "reason": reported_reason,
            "capacity_gap": state["capacity_gap"],
            "queued_attempts": state["queued_attempts"],
            "idle_runners": state["idle_runners"],
            "starting_instances": state["starting_instances"],
            "current_size": state["current_size"],
            "desired_capacity": state["desired_capacity"],
            "price_usd": state["price_usd"],
            "price_threshold_usd": state["price_threshold_usd"],
            "checked_at": _iso(now),
            "next_check_at": _iso(pool.next_check_at),
        },
        dedupe_key=_state_dedupe_key(pool, state, reported_reason),
    )

    state.update(
        {
            "capacity_reason": reported_reason,
            "last_checked_at": _iso(now),
            "next_check_at": _iso(pool.next_check_at),
            "scaled_up": scaled_up,
            "scaled_down": scaled_down,
            "provider_sync": synced,
        }
    )
    return state


def reconcile_pools(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    provisioners: Mapping[str, Provisioner] | None = None,
    now_after: datetime | None = None,
) -> dict[str, int]:
    """Reconcile every active spot pool, committing once per pool.

    ``provisioners`` overrides the module registry (tests, worker bootstrap).
    ``now_after`` skips pools whose ``next_check_at`` is still in the future —
    the worker passes the tick time, so a slow pass does not double-spend the
    check interval. One pool's failure never stops the sweep.
    """

    now = now or datetime.now(UTC)
    due = now_after if now_after is not None else now
    counts = {
        "scanned": 0,
        "scaled_up": 0,
        "scaled_down": 0,
        "waiting_price": 0,
        "errors": 0,
    }

    pools = list(
        session.scalars(
            select(ResourcePool)
            .where(
                ResourcePool.status == "active",
                ResourcePool.provisioning_mode == PROVISIONING_MODE_SPOT,
            )
            .order_by(ResourcePool.id)
        )
    )
    for pool in pools:
        # Re-read under a row lock: next_check_at is a read-decide-write field,
        # so without this two workers could both create instances for one gap.
        # Postgres honours FOR UPDATE; SQLite serialises writes anyway.
        locked = session.get(ResourcePool, pool.id, with_for_update=True)
        if locked is not None:
            pool = locked
        next_check = _aware(pool.next_check_at)
        if next_check is not None and next_check > _aware(due):
            continue
        counts["scanned"] += 1
        provisioner = (
            provisioners.get(pool.provider_key)
            if provisioners is not None
            else get_provisioner(pool.provider_key)
        )
        try:
            state = reconcile_pool(
                session, pool, settings=settings, now=now, provisioner=provisioner
            )
            session.commit()
        except Exception:  # pragma: no cover - one bad pool must not stop the sweep
            session.rollback()
            counts["errors"] += 1
            continue
        if state.get("scaled_up"):
            counts["scaled_up"] += 1
        if state.get("scaled_down"):
            counts["scaled_down"] += 1
        if state.get("capacity_reason") == REASON_PRICE_ABOVE_THRESHOLD:
            counts["waiting_price"] += 1
    return counts

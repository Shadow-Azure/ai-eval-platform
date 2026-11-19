"""Spot-to-fixed/on-demand fallback after repeated supply failures (M8-46 #311).

The zero-resident spot pool deliberately retries expensive ephemeral
instances, but an availability window can be long enough that a case exhausts
its attempt budget before any spot instance survives. This module owns the
run-level fallback switch:

* count consecutive provider-side spot failures per run (a successful case
  resets the streak);
* count consecutive spot supply blocks (inventory missing / price above the
  pool threshold) while a run still has waiting attempts;
* once a configured threshold is reached, move the run to an existing
  fixed/on-demand pool, record the fallback in the run configuration snapshot
  and resource events, and retarget every not-yet-dispatched shard so the next
  jobs run on the fallback pool.

The platform does not create a new cloud provider or a new pool: the operator
configures an existing pool as ``fallback_pool_id`` in the spot pool's
``instance_config``. A ``fallback_max_price_cny`` cap is enforced against the
fallback pool's declared hourly cost (``reference_price_cny`` / ``price_cny`` /
``price_threshold_cny``); with a cap configured, an unknown fallback cost is
rejected rather than silently spending money.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import (
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    Outbox,
    ResourcePool,
)
from ..money import Money, MoneyError
from .evaluations import SHARD_DISPATCH_TOPIC
from .runner_labels import DEFAULT_RUNS_ON, pool_runner_labels

SNAPSHOT_KEY = "spot_fallback"
REASON_PROVIDER_RECLAIM = "provider_spot_reclaim"
REASON_INVENTORY = "inventory_insufficient"
REASON_PRICE = "price_above_threshold"

EVENT_RUN_FALLBACK = "eval.spot_fallback"
EVENT_RESOURCE_FALLBACK = "resource.spot.fallback"
EVENT_RESOURCE_SUPPRESSED = "resource.spot.fallback_suppressed"

FALLBACK_AFTER_MIN = 1
FALLBACK_AFTER_MAX = 20

#: Attempts that have not been released to GitHub yet. Only these can be
#: retargeted by the fallback switch; a job already sent to the old spot pool
#: keeps running there and its retry is built with the new run pool.
PENDING_ATTEMPT_STATUSES = ("pending",)


@dataclass(frozen=True)
class FallbackConfig:
    pool_id: str
    after: int
    max_price_cny: Decimal | None


class FallbackConfigError(ValueError):
    """Invalid operator-supplied fallback configuration (mapped to HTTP 422)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.field = field
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True)
class FallbackDecision:
    """Outcome of one failure observation for the triggering run."""

    active: bool = False
    activated: bool = False
    from_pool_id: str | None = None
    to_pool_id: str | None = None
    to_pool_name: str | None = None
    reason: str | None = None
    failures: int = 0
    suppressed_reason: str | None = None

    @property
    def summary(self) -> str:
        if self.to_pool_name:
            return f"已回退到 {self.to_pool_name}"
        return ""


def _now_iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="microseconds")


def _state(run: EvalRun) -> dict[str, Any]:
    snapshot = run.resource_pool_snapshot_json
    if not isinstance(snapshot, dict):
        return {}
    state = snapshot.get(SNAPSHOT_KEY)
    return copy.deepcopy(state) if isinstance(state, dict) else {}


def _write_state(run: EvalRun, state: dict[str, Any]) -> None:
    snapshot = copy.deepcopy(dict(run.resource_pool_snapshot_json or {}))
    # Deep-copy the fallback state as well: an in-place update after a previous
    # assignment would make SQLAlchemy compare the mutated old value with
    # itself and skip the JSON UPDATE.
    snapshot[SNAPSHOT_KEY] = copy.deepcopy(state)
    run.resource_pool_snapshot_json = snapshot


def run_fallback(run: EvalRun) -> dict[str, Any] | None:
    """Normalized fallback state for the run configuration API, or None."""

    state = _state(run)
    if not state:
        return None
    return {
        "active": bool(state.get("active")),
        "from_pool_id": state.get("from_pool_id"),
        "from_pool_name": state.get("from_pool_name"),
        "to_pool_id": state.get("to_pool_id"),
        "to_pool_name": state.get("to_pool_name"),
        "to_provisioning_mode": state.get("to_provisioning_mode"),
        "reason": state.get("reason"),
        "failures": int(state.get("consecutive_failures") or 0),
        "supply_blocks": int(state.get("consecutive_supply_blocks") or 0),
        "fallback_after": state.get("fallback_after"),
        "max_price_cny": state.get("max_price_cny"),
        "fallback_price_cny": state.get("fallback_price_cny"),
        "suppressed_reason": state.get("suppressed_reason"),
        "activated_at": state.get("activated_at"),
        "last_failure_at": state.get("last_failure_at"),
    }


def _instance_config(pool: ResourcePool | None) -> dict[str, Any]:
    if pool is None or not isinstance(pool.instance_config_json, dict):
        return {}
    return dict(pool.instance_config_json)


def _positive_price(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value > 0 else None


def fallback_config(
    pool: ResourcePool | None, *, settings: Settings | None = None
) -> FallbackConfig | None:
    """Read the optional fallback target from a spot pool's instance config."""

    if pool is None or pool.provisioning_mode != "spot":
        return None
    config = _instance_config(pool)
    pool_id = config.get("fallback_pool_id")
    if not isinstance(pool_id, str) or not pool_id.strip():
        return None
    settings = settings or get_settings()
    raw_after = config.get("fallback_after", settings.eval_spot_fallback_after)
    try:
        after = int(raw_after)
    except (TypeError, ValueError):
        after = int(settings.eval_spot_fallback_after)
    after = min(FALLBACK_AFTER_MAX, max(FALLBACK_AFTER_MIN, after))
    max_price = _positive_price(
        config.get("fallback_max_price_cny", settings.eval_spot_fallback_max_price_cny)
    )
    return FallbackConfig(pool_id=pool_id.strip(), after=after, max_price_cny=max_price)


def validate_fallback_config(
    session: Session,
    *,
    tenant_id: str,
    pool_id: str | None,
    provisioning_mode: str,
    instance_config: dict[str, Any] | None,
) -> None:
    """Reject an invalid operator fallback before it reaches the controller."""

    config = dict(instance_config or {})
    fallback_pool_id = config.get("fallback_pool_id")
    has_after = config.get("fallback_after") not in (None, "")
    has_cap = config.get("fallback_max_price_cny") not in (None, "")
    reference_price = config.get("reference_price_cny")
    if reference_price not in (None, "") and _positive_price(reference_price) is None:
        raise FallbackConfigError(
            "spot_fallback_reference_price_invalid",
            "兜底资源池参考成本必须是正数（CNY/小时）",
            field="instance_config.reference_price_cny",
        )
    if not fallback_pool_id:
        if has_after or has_cap:
            raise FallbackConfigError(
                "spot_fallback_target_missing",
                "配置回退阈值或成本上限时必须同时选择兜底资源池",
                field="instance_config.fallback_pool_id",
            )
        return
    if provisioning_mode != "spot":
        raise FallbackConfigError(
            "spot_fallback_requires_spot_pool",
            "只有竞价实例资源池支持 spot 回退配置",
            field="instance_config.fallback_pool_id",
        )
    if not isinstance(fallback_pool_id, str) or len(fallback_pool_id) > 36:
        raise FallbackConfigError(
            "spot_fallback_pool_invalid",
            "兜底资源池 ID 必须是 1..36 字符",
            field="instance_config.fallback_pool_id",
        )
    target = session.get(ResourcePool, fallback_pool_id)
    if target is None or target.tenant_id != tenant_id:
        raise FallbackConfigError(
            "spot_fallback_pool_not_found",
            "兜底资源池不存在或不属于当前项目",
            field="instance_config.fallback_pool_id",
        )
    if pool_id is not None and target.id == pool_id:
        raise FallbackConfigError(
            "spot_fallback_pool_is_self",
            "兜底资源池不能是当前竞价资源池本身",
            field="instance_config.fallback_pool_id",
        )
    if target.provisioning_mode != "fixed":
        raise FallbackConfigError(
            "spot_fallback_pool_not_fixed",
            "兜底资源池必须是固定/按量资源池",
            field="instance_config.fallback_pool_id",
        )
    if target.status != "active":
        raise FallbackConfigError(
            "spot_fallback_pool_inactive",
            "兜底资源池必须处于启用状态",
            field="instance_config.fallback_pool_id",
        )
    if has_after:
        try:
            after = int(config["fallback_after"])
        except (TypeError, ValueError):
            after = -1
        if not FALLBACK_AFTER_MIN <= after <= FALLBACK_AFTER_MAX:
            raise FallbackConfigError(
                "spot_fallback_after_invalid",
                f"回退阈值必须是 {FALLBACK_AFTER_MIN}..{FALLBACK_AFTER_MAX} 的整数",
                field="instance_config.fallback_after",
            )
    if has_cap and _positive_price(config.get("fallback_max_price_cny")) is None:
        raise FallbackConfigError(
            "spot_fallback_max_price_invalid",
            "回退成本上限必须是正数（CNY/小时）",
            field="instance_config.fallback_max_price_cny",
        )
    if has_cap and fallback_reference_price(target) is None:
        raise FallbackConfigError(
            "spot_fallback_cost_unknown",
            "启用成本上限时，兜底资源池必须先声明参考成本（CNY/小时）",
            field="instance_config.fallback_max_price_cny",
        )


def fallback_reference_price(pool: ResourcePool) -> Decimal | None:
    """Hourly cost declared for the fallback pool, if the operator set one."""

    config = _instance_config(pool)
    for raw in (
        config.get("reference_price_cny"),
        config.get("price_cny"),
        pool.price_cny,
        pool.price_threshold_cny,
    ):
        price = _positive_price(raw)
        if price is not None:
            return price
    return None


def _fallback_labels(
    session: Session, run: EvalRun, target: ResourcePool, settings: Settings
) -> list[str]:
    if target.provisioning_mode == "spot":
        fallback = [
            item.strip()
            for item in settings.runner_spot_labels.split(",")
            if item.strip()
        ]
    else:
        fallback = list(DEFAULT_RUNS_ON)
    return pool_runner_labels(target, fallback=fallback)


def _retarget_pending_dispatches(
    session: Session,
    *,
    run: EvalRun,
    target: ResourcePool,
    labels: list[str],
    state: dict[str, Any],
) -> int:
    """Rewrite pending outbox payloads to the fallback runner labels.

    A shard already claimed by the dispatcher (``processing``/``sent``) cannot
    be moved safely; it keeps its original pool and a later retry is built from
    the run's new pool. Rewriting the manifest and digest together keeps the
    runner contract's byte-stable manifest check valid.
    """

    rows = list(
        session.scalars(
            select(Outbox).where(
                Outbox.aggregate_type == "eval_run",
                Outbox.aggregate_id == run.id,
                Outbox.topic == SHARD_DISPATCH_TOPIC,
                Outbox.status == "pending",
            )
        )
    )
    changed = 0
    for row in rows:
        payload = dict(row.payload_json or {})
        client_payload = dict(payload.get("client_payload") or {})
        if not client_payload:
            continue
        client_payload["runs_on"] = labels
        client_payload["spot_fallback"] = {
            "active": True,
            "to_pool_id": target.id,
            "to_pool_name": target.name,
        }
        payload["client_payload"] = client_payload
        manifest_text = payload.get("manifest_text")
        if isinstance(manifest_text, str) and manifest_text:
            manifest = json.loads(manifest_text)
            manifest["runs_on"] = labels
            manifest["spot_fallback"] = {
                "active": True,
                "to_pool_id": target.id,
                "to_pool_name": target.name,
            }
            manifest_text = json.dumps(
                manifest, ensure_ascii=False, indent=2, sort_keys=True
            )
            digest = "sha256:" + hashlib.sha256(manifest_text.encode()).hexdigest()
            payload["manifest_text"] = manifest_text
            payload["manifest_digest"] = digest
            client_payload["manifest_digest"] = digest
        payload["fallback"] = {
            "from_pool_id": state.get("from_pool_id"),
            "to_pool_id": target.id,
            "activated_at": state.get("activated_at"),
        }
        row.payload_json = payload
        changed += 1
    if changed:
        session.flush()
    return changed


def _retarget_pending_attempts(
    session: Session, *, run: EvalRun, target: ResourcePool
) -> int:
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseAttempt.status.in_(PENDING_ATTEMPT_STATUSES),
            )
        )
    )
    for attempt in attempts:
        attempt.resource_pool_id = target.id
    if attempts:
        session.flush()
    return len(attempts)


def _record_suppressed(
    session: Session,
    *,
    source: ResourcePool,
    run: EvalRun,
    reason: str,
    state: dict[str, Any],
    now: datetime,
) -> None:
    from .capacity_control import append_resource_event

    state["suppressed_reason"] = reason
    state["suppressed_at"] = _now_iso(now)
    _write_state(run, state)
    append_resource_event(
        session,
        source,
        EVENT_RESOURCE_SUPPRESSED,
        {
            "run_id": run.id,
            "reason": reason,
            "fallback_after": state.get("fallback_after"),
            "consecutive_failures": state.get("consecutive_failures"),
            "consecutive_supply_blocks": state.get("consecutive_supply_blocks"),
        },
        dedupe_key=f"{source.id}:spot_fallback_suppressed:{run.id}:{reason}",
    )


def _try_activate(
    session: Session,
    *,
    run: EvalRun,
    source: ResourcePool,
    config: FallbackConfig,
    reason: str,
    state: dict[str, Any],
    now: datetime,
    settings: Settings,
) -> FallbackDecision:
    target = session.get(ResourcePool, config.pool_id)
    if target is None or target.tenant_id != run.tenant_id:
        _record_suppressed(
            session,
            source=source,
            run=run,
            reason="fallback_pool_missing",
            state=state,
            now=now,
        )
        return FallbackDecision(
            failures=int(state.get("consecutive_failures") or 0),
            suppressed_reason="fallback_pool_missing",
        )
    if target.id == source.id:
        _record_suppressed(
            session,
            source=source,
            run=run,
            reason="fallback_pool_is_self",
            state=state,
            now=now,
        )
        return FallbackDecision(
            failures=int(state.get("consecutive_failures") or 0),
            suppressed_reason="fallback_pool_is_self",
        )
    if target.provisioning_mode != "fixed":
        _record_suppressed(
            session,
            source=source,
            run=run,
            reason="fallback_pool_not_fixed",
            state=state,
            now=now,
        )
        return FallbackDecision(
            failures=int(state.get("consecutive_failures") or 0),
            suppressed_reason="fallback_pool_not_fixed",
        )
    if target.status != "active":
        _record_suppressed(
            session,
            source=source,
            run=run,
            reason="fallback_pool_inactive",
            state=state,
            now=now,
        )
        return FallbackDecision(
            failures=int(state.get("consecutive_failures") or 0),
            suppressed_reason="fallback_pool_inactive",
        )

    fallback_price = fallback_reference_price(target)
    if config.max_price_cny is not None:
        if fallback_price is None:
            _record_suppressed(
                session,
                source=source,
                run=run,
                reason="fallback_cost_unknown",
                state=state,
                now=now,
            )
            return FallbackDecision(
                failures=int(state.get("consecutive_failures") or 0),
                suppressed_reason="fallback_cost_unknown",
            )
        try:
            exceeds_cap = Money.of(fallback_price) > Money.of(config.max_price_cny)
        except MoneyError:
            exceeds_cap = True
        if exceeds_cap:
            _record_suppressed(
                session,
                source=source,
                run=run,
                reason="fallback_cost_cap_exceeded",
                state=state,
                now=now,
            )
            return FallbackDecision(
                failures=int(state.get("consecutive_failures") or 0),
                suppressed_reason="fallback_cost_cap_exceeded",
            )

    previous_pool_id = run.resource_pool_id
    run.resource_pool_id = target.id
    run.effective_concurrency = None
    run.queue_reason = None
    labels = _fallback_labels(session, run, target, settings)
    state.update(
        {
            "active": True,
            "from_pool_id": source.id,
            "from_pool_name": source.name,
            "to_pool_id": target.id,
            "to_pool_name": target.name,
            "to_provisioning_mode": target.provisioning_mode,
            "reason": reason,
            "fallback_after": config.after,
            "max_price_cny": str(config.max_price_cny) if config.max_price_cny else None,
            "fallback_price_cny": str(fallback_price) if fallback_price else None,
            "activated_at": _now_iso(now),
        }
    )
    state.pop("suppressed_reason", None)
    state.pop("suppressed_at", None)
    _write_state(run, state)
    retargeted_attempts = _retarget_pending_attempts(session, run=run, target=target)
    retargeted_dispatches = _retarget_pending_dispatches(
        session, run=run, target=target, labels=labels, state=state
    )

    payload = {
        "from_pool_id": source.id,
        "from_pool_name": source.name,
        "to_pool_id": target.id,
        "to_pool_name": target.name,
        "to_provisioning_mode": target.provisioning_mode,
        "reason": reason,
        "fallback_after": config.after,
        "max_price_cny": state.get("max_price_cny"),
        "fallback_price_cny": state.get("fallback_price_cny"),
        "consecutive_failures": int(state.get("consecutive_failures") or 0),
        "consecutive_supply_blocks": int(state.get("consecutive_supply_blocks") or 0),
        "retargeted_attempts": retargeted_attempts,
        "retargeted_dispatches": retargeted_dispatches,
        "runs_on": labels,
        "activated_at": state.get("activated_at"),
    }
    session.add(
        EvalEvent(
            tenant_id=run.tenant_id,
            eval_run_id=run.id,
            event_type=EVENT_RUN_FALLBACK,
            payload_json=payload,
        )
    )
    from .capacity_control import append_resource_event

    append_resource_event(
        session,
        source,
        EVENT_RESOURCE_FALLBACK,
        {"run_id": run.id, **payload},
        dedupe_key=f"{source.id}:spot_fallback:{run.id}",
    )
    append_resource_event(
        session,
        target,
        EVENT_RESOURCE_FALLBACK,
        {"run_id": run.id, **payload},
        dedupe_key=f"{target.id}:spot_fallback:{run.id}",
    )
    if previous_pool_id != target.id:
        session.flush()
    return FallbackDecision(
        active=True,
        activated=True,
        from_pool_id=source.id,
        to_pool_id=target.id,
        to_pool_name=target.name,
        reason=reason,
        failures=int(state.get("consecutive_failures") or 0),
    )


def _active_decision(state: dict[str, Any]) -> FallbackDecision:
    return FallbackDecision(
        active=True,
        from_pool_id=state.get("from_pool_id"),
        to_pool_id=state.get("to_pool_id"),
        to_pool_name=state.get("to_pool_name"),
        reason=state.get("reason"),
        failures=int(state.get("consecutive_failures") or 0),
    )


def record_spot_reclaim(
    session: Session,
    *,
    run: EvalRun,
    reason: str,
    now: datetime,
    settings: Settings | None = None,
) -> FallbackDecision:
    """Observe one provider-side spot reclaim and maybe activate fallback."""

    settings = settings or get_settings()
    if run.resource_pool_id is None:
        return FallbackDecision()
    state = _state(run)
    if state.get("active"):
        return _active_decision(state)
    source = session.get(ResourcePool, state.get("from_pool_id") or run.resource_pool_id)
    config = fallback_config(source, settings=settings)
    if config is None:
        return FallbackDecision()
    failures = int(state.get("consecutive_failures") or 0) + 1
    state["consecutive_failures"] = failures
    state["last_failure_at"] = _now_iso(now)
    state["last_failure_reason"] = f"{REASON_PROVIDER_RECLAIM}:{reason}"
    state["from_pool_id"] = source.id
    state["from_pool_name"] = source.name
    state["fallback_after"] = config.after
    _write_state(run, state)
    if failures < config.after:
        return FallbackDecision(failures=failures)
    return _try_activate(
        session,
        run=run,
        source=source,
        config=config,
        reason=REASON_PROVIDER_RECLAIM,
        state=state,
        now=now,
        settings=settings,
    )


def record_supply_block(
    session: Session,
    *,
    run: EvalRun,
    pool: ResourcePool,
    reason: str,
    now: datetime,
    settings: Settings | None = None,
) -> FallbackDecision:
    """Observe one reconcile tick where the spot pool could not add capacity."""

    settings = settings or get_settings()
    config = fallback_config(pool, settings=settings)
    if config is None:
        return FallbackDecision()
    state = _state(run)
    if state.get("active"):
        return _active_decision(state)
    blocks = int(state.get("consecutive_supply_blocks") or 0) + 1
    state["consecutive_supply_blocks"] = blocks
    state["last_supply_block_at"] = _now_iso(now)
    state["last_supply_block_reason"] = reason
    state["from_pool_id"] = pool.id
    state["from_pool_name"] = pool.name
    state["fallback_after"] = config.after
    _write_state(run, state)
    if blocks < config.after:
        return FallbackDecision(failures=blocks)
    activation_reason = (
        REASON_PRICE if reason.endswith("price_above_threshold") else REASON_INVENTORY
    )
    return _try_activate(
        session,
        run=run,
        source=pool,
        config=config,
        reason=activation_reason,
        state=state,
        now=now,
        settings=settings,
    )


def reset_supply_blocks(session: Session, *, run: EvalRun, now: datetime) -> None:
    """A pool that can add capacity again breaks the supply-block streak."""

    state = _state(run)
    if state.get("active") or not state.get("consecutive_supply_blocks"):
        return
    state["consecutive_supply_blocks"] = 0
    state["last_supply_block_reset_at"] = _now_iso(now)
    _write_state(run, state)


def reset_after_success(session: Session, *, run: EvalRun, now: datetime) -> None:
    """A completed case proves the current pool works; restart the streak."""

    state = _state(run)
    if state.get("active") or not (
        state.get("consecutive_failures") or state.get("consecutive_supply_blocks")
    ):
        return
    state["consecutive_failures"] = 0
    state["consecutive_supply_blocks"] = 0
    state["last_success_at"] = _now_iso(now)
    _write_state(run, state)


def waiting_runs_for_pool(session: Session, pool: ResourcePool) -> list[EvalRun]:
    """Runs with attempts still waiting for a runner in ``pool``.

    Attempts keep their original ``resource_pool_id`` even after the run moves
    to a fallback pool, so the supply-block observer can still see a run that
    has a shard queued on the spot side.
    """

    statement = (
        select(EvalRun)
        .join(EvalCaseRun, EvalCaseRun.eval_run_id == EvalRun.id)
        .join(EvalCaseAttempt, EvalCaseAttempt.eval_case_run_id == EvalCaseRun.id)
        .where(
            EvalRun.status.in_(("queued", "running")),
            EvalCaseAttempt.status.in_(("pending", "dispatching", "queued")),
            or_(
                EvalRun.resource_pool_id == pool.id,
                EvalCaseAttempt.resource_pool_id == pool.id,
            ),
        )
        .distinct()
        .order_by(EvalRun.id)
    )
    return list(session.scalars(statement))

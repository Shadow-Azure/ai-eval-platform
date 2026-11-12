"""Expiring launch preparations (M5-04, design 04 §3.1 / 09 §5.1).

``prepare`` never creates an evaluation or a cloud instance. It resolves the
operator's selection into a frozen snapshot — case subset, asset commit,
runtime, model credential, resource pool and execution policy — and stores it
with a short TTL. ``create`` accepts only that snapshot id, so the run always
matches what was confirmed. The configuration identity is re-derived from the
database at create time; a moved branch, revoked credential, inactive pool or
changed image default yields ``configuration_changed`` instead of silently
launching with different inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..defaults import DEFAULT_TENANT_ID
from ..models import (
    AgentAssetSnapshot,
    Credential,
    Dataset,
    DatasetCase,
    DatasetVersion,
    EvalPreparation,
    EvalRun,
    ModelConfig,
    ResourcePool,
    new_id,
)
from ..money import UNIT_PRICE, Money, MoneyError, money_str
from ..runtimes.claude_code import get_runtime_adapter
from . import capacity
from . import evaluations as evaluation_service
from .preflight import PreflightIssue, check_preparation

CASE_SELECTION_MODES = ("all", "count", "cases")
DEFAULT_CASE_TIMEOUT_SECONDS = 3600
DEFAULT_MAX_ATTEMPTS = 2


class PreparationError(Exception):
    """Base class; subclasses carry the HTTP mapping used by the API."""

    status = 422
    code = "preparation_invalid"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        self.details = details or {}
        super().__init__(message)


class PreparationNotFound(PreparationError):
    status = 404
    code = "preparation_not_found"


class PreparationExpired(PreparationError):
    status = 409
    code = "preparation_expired"


class PreparationConsumed(PreparationError):
    status = 409
    code = "preparation_consumed"


class ConfigurationChanged(PreparationError):
    status = 409
    code = "configuration_changed"

    def __init__(self, changed: list[str]) -> None:
        self.changed = changed
        super().__init__(
            "配置已变化，请回到确认页重新准备",
            details={"changed": changed},
        )


class PreparationBlocked(PreparationError):
    """The frozen configuration no longer passes consistency checks."""

    status = 422
    code = "preparation_blocked"

    def __init__(self, blockers: list[dict]) -> None:
        self.blockers = blockers
        super().__init__("准备快照存在阻塞项", details={"blockers": blockers})


class PreparationInvalid(PreparationError):
    status = 422
    code = "evaluation_invalid"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        self.field = field
        super().__init__(message, details={"fields": {field: "invalid"}} if field else {})


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive timestamps; treat them as UTC like the rest of the API."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest(payload: object) -> str:
    text = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _pick(value, default):
    return default if value is None else value


def _effective_image_digest(
    provided: str | None, bound: str | None, fallback: str | None
) -> str | None:
    for candidate in (provided, bound, fallback):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _case_selection(selection: dict | None) -> dict:
    selection = dict(selection or {})
    mode = str(selection.get("mode") or "all")
    if mode not in CASE_SELECTION_MODES:
        raise PreparationInvalid(f"不支持的用例选择方式：{mode}", field="case_selection")
    count = selection.get("count")
    case_keys = selection.get("case_keys")
    if count is not None:
        try:
            count = int(count)
        except (TypeError, ValueError) as exc:
            raise PreparationInvalid("用例数量必须是正整数", field="case_selection") from exc
    if case_keys is not None and not isinstance(case_keys, list):
        raise PreparationInvalid("case_keys 必须是字符串列表", field="case_selection")
    return {
        "mode": mode,
        "count": count,
        "case_keys": [str(key) for key in case_keys] if case_keys else None,
    }


def resolve_case_keys(
    session: Session,
    version: DatasetVersion,
    selection: dict,
    settings: Settings,
) -> list[str]:
    """Resolve the selection against the immutable dataset version."""

    selection = _case_selection(selection)
    cases = list(
        session.scalars(
            select(DatasetCase)
            .where(DatasetCase.dataset_version_id == version.id)
            .order_by(DatasetCase.ordinal, DatasetCase.case_key)
        )
    )
    if not cases:
        raise PreparationInvalid("数据集版本没有用例", field="dataset_version_id")
    if len(cases) > settings.eval_max_cases:
        raise PreparationInvalid(
            f"单个数据集版本最多选择 {settings.eval_max_cases} 个用例",
            field="dataset_version_id",
        )
    if selection["mode"] == "all":
        return [case.case_key for case in cases]
    if selection["mode"] == "count":
        count = selection["count"]
        if count is None or count < 1:
            raise PreparationInvalid("按数量选择时必须提供正整数数量", field="case_selection")
        if count > len(cases):
            raise PreparationInvalid(
                f"选择数量 {count} 超过版本用例总数 {len(cases)}",
                field="case_selection",
            )
        return [case.case_key for case in cases[:count]]
    wanted = selection["case_keys"] or []
    if not wanted:
        raise PreparationInvalid("按用例选择时至少选择 1 个用例", field="case_selection")
    if len(set(wanted)) != len(wanted):
        raise PreparationInvalid("所选用例存在重复项", field="case_selection")
    available = {case.case_key for case in cases}
    missing = sorted(set(wanted) - available)
    if missing:
        raise PreparationInvalid(
            "所选用例不属于该数据集版本：" + ", ".join(missing[:5]),
            field="case_selection",
        )
    wanted_set = set(wanted)
    return [case.case_key for case in cases if case.case_key in wanted_set]


def estimate_cost(
    *,
    pricing: dict,
    case_count: int,
    effective_concurrency: int,
    pool: ResourcePool | None,
    settings: Settings,
) -> dict | None:
    """Model-cost and wall-clock range labelled with its assumptions.

    This is a planning estimate only; the model gateway records actual spend.
    When pricing is missing the estimate is omitted rather than invented.
    """

    try:
        input_rate = Money.of((pricing or {}).get("input_per_1m") or 0)
        output_rate = Money.of((pricing or {}).get("output_per_1m") or 0)
    except MoneyError:
        return None
    if input_rate <= 0 and output_rate <= 0:
        return None

    input_tokens = max(0, settings.eval_estimate_input_tokens_per_case)
    output_tokens = max(0, settings.eval_estimate_output_tokens_per_case)
    per_case = (
        input_rate * Decimal(input_tokens) / Decimal(1_000_000)
        + output_rate * Decimal(output_tokens) / Decimal(1_000_000)
    )
    spread = Decimal(str(max(0.0, float(settings.eval_estimate_spread))))
    total = per_case * case_count
    model_low = (total * (Decimal(1) - spread)).quantize(UNIT_PRICE)
    model_high = (total * (Decimal(1) + spread)).quantize(UNIT_PRICE)

    parallel = max(1, int(effective_concurrency or 0))
    waves = math.ceil(case_count / parallel) if case_count else 0
    duration_mid = waves * max(0, settings.eval_estimate_case_seconds)
    duration_low = int(duration_mid * 0.8)
    duration_high = int(duration_mid * 1.5)

    runner_low = runner_high = None
    pool_price = pool.price_cny if pool is not None else None
    if pool_price is not None:
        price = Money.of(pool_price)
        runner_low = (price * duration_low / 3600).quantize(UNIT_PRICE)
        runner_high = (price * duration_high / 3600).quantize(UNIT_PRICE)

    total_low = model_low + (runner_low or Money.zero())
    total_high = model_high + (runner_high or Money.zero())
    pricing_basis = (
        f"按 {input_tokens:,} 输入 + {output_tokens:,} 输出 token/用例 × {case_count} 用例，"
        f"输入 {input_rate.format()} /百万、输出 {output_rate.format()} /百万估算"
    )
    if runner_low is not None:
        pricing_basis += "；含资源池机时，按估算时长与当前价格"
    else:
        pricing_basis += "；不含机时"
    return {
        "currency": "CNY",
        "model_cny": {"low": money_str(model_low), "high": money_str(model_high)},
        "runner_cny": (
            {"low": money_str(runner_low), "high": money_str(runner_high)}
            if runner_low is not None and runner_high is not None
            else None
        ),
        "total_cny": {"low": money_str(total_low), "high": money_str(total_high)},
        "estimated_duration_seconds": {"low": duration_low, "high": duration_high},
        "basis": pricing_basis,
        "assumptions": {
            "input_tokens_per_case": input_tokens,
            "output_tokens_per_case": output_tokens,
            "case_seconds": settings.eval_estimate_case_seconds,
            "spread": float(spread),
        },
    }


def _snapshot_asset(asset: dict | None) -> dict | None:
    if not asset:
        return None
    return {
        "id": asset.get("id"),
        # Freeze the full bundle descriptor: the runner builds its task
        # payload from this JSON and needs snapshot_id/bundle_key to fetch it.
        "snapshot_id": asset.get("snapshot_id") or asset.get("id"),
        "source_id": asset.get("source_id"),
        "requested_ref": asset.get("requested_ref"),
        "resolved_commit": asset.get("resolved_commit"),
        "tree_digest": asset.get("tree_digest"),
        "bundle_key": asset.get("bundle_key"),
        "bundle_digest": asset.get("bundle_digest"),
        "bundle_size": asset.get("bundle_size"),
        "file_count": asset.get("file_count"),
        "asset_count": asset.get("asset_count"),
        "runtime_key": asset.get("runtime_key"),
        "runtime_prerequisites": asset.get("runtime_prerequisites") or {},
        "load_test": asset.get("load_test") or {},
        "assets": asset.get("assets") or [],
    }


def _live_identity(
    session: Session,
    snapshot: dict,
    settings: Settings,
) -> dict:
    """Derive the configuration identity from live rows for comparison."""

    tenant_id = str(DEFAULT_TENANT_ID)
    dataset_info = snapshot.get("dataset") or {}
    version = session.get(DatasetVersion, dataset_info.get("id"))
    if version is None or version.tenant_id != tenant_id:
        version = None
    dataset = session.get(Dataset, version.dataset_id) if version is not None else None
    if dataset is not None and dataset.tenant_id != tenant_id:
        dataset = None

    model_info = snapshot.get("model") or {}
    config = session.get(ModelConfig, model_info.get("config_id"))
    if config is not None and config.tenant_id != tenant_id:
        config = None
    credential = (
        session.get(Credential, config.credential_id)
        if config is not None and config.credential_id
        else None
    )

    pool_info = snapshot.get("resource_pool") or {}
    pool = session.get(ResourcePool, pool_info.get("id"))
    if pool is not None and pool.tenant_id != tenant_id:
        pool = None

    asset_info = snapshot.get("asset_snapshot") or {}
    asset_id = asset_info.get("id") if isinstance(asset_info, dict) else None
    asset = session.get(AgentAssetSnapshot, asset_id) if asset_id else None
    if asset is not None and asset.tenant_id != tenant_id:
        asset = None

    execution = snapshot.get("execution") or {}
    selection = snapshot.get("case_selection") or {}
    runtime_info = snapshot.get("runtime") or {}
    adapter = get_runtime_adapter(str(runtime_info.get("key") or "claude-code"))

    resolved_execution = _effective_image_digest(
        execution.get("execution_image_digest"),
        version.execution_image_digest if version is not None else None,
        settings.default_execution_image_digest,
    )
    resolved_scoring = _effective_image_digest(
        execution.get("scoring_image_digest"),
        version.scoring_image_digest if version is not None else None,
        settings.default_scoring_image_digest,
    )

    return {
        "dataset": {
            "id": version.id if version is not None else dataset_info.get("id"),
            "status": version.status if version is not None else None,
            "case_count": version.case_count if version is not None else None,
            "source_revision": version.source_revision if version is not None else None,
            "execution_image_digest": (
                version.execution_image_digest if version is not None else None
            ),
            "scoring_image_digest": version.scoring_image_digest if version is not None else None,
            "adapter_key": dataset.adapter_key if dataset is not None else None,
        },
        "case_selection": {
            "mode": selection.get("mode"),
            "count": selection.get("count"),
            "explicit_digest": (
                _digest(sorted(selection.get("case_keys") or []))
                if selection.get("mode") == "cases"
                else None
            ),
            "case_count": snapshot.get("case_count"),
        },
        # M8-07: the appended instructions are part of the confirmed launch
        # configuration, so a changed suffix invalidates the preparation.
        "prompt_suffix": snapshot.get("prompt_suffix") or "",
        "runtime": {
            "key": runtime_info.get("key"),
            "adapter_version": adapter.runtime_version if adapter is not None else None,
        },
        "model": {
            "config_id": config.id if config is not None else model_info.get("config_id"),
            "model_name": config.model_name if config is not None else None,
            "base_url": config.base_url if config is not None else None,
            "credential_id": config.credential_id if config is not None else None,
            "revoked_at": _iso(credential.revoked_at) if credential is not None else None,
        },
        "resource_pool": {
            "id": pool.id if pool is not None else pool_info.get("id"),
            "status": pool.status if pool is not None else None,
        },
        "asset_snapshot": (
            {
                "id": asset.id if asset is not None else asset_id,
                "resolved_commit": asset.resolved_commit if asset is not None else None,
                "tree_digest": asset.tree_digest if asset is not None else None,
                "asset_count": asset.asset_count if asset is not None else None,
                "runtime_key": asset.runtime_key if asset is not None else None,
                "validation_ok": bool(
                    (asset.manifest_json or {}).get("validation", {}).get("ok")
                )
                if asset is not None
                else False,
            }
            if asset_id
            else None
        ),
        "execution": {
            "concurrency": execution.get("concurrency"),
            "case_timeout_seconds": execution.get("case_timeout_seconds"),
            "max_attempts": execution.get("max_attempts"),
            "model_budget_cny": money_str(execution.get("model_budget_cny")),
            "shard_size": execution.get("shard_size"),
        },
        "resolved_images": {
            "execution": resolved_execution,
            "scoring": resolved_scoring,
        },
    }


def _diff(identity: dict, live: dict, prefix: str = "") -> list[str]:
    changed: list[str] = []
    for key in sorted(set(identity) | set(live)):
        path = f"{prefix}.{key}" if prefix else key
        left = identity.get(key) if isinstance(identity, dict) else None
        right = live.get(key) if isinstance(live, dict) else None
        if isinstance(left, dict) and isinstance(right, dict):
            changed.extend(_diff(left, right, path))
        elif left != right:
            changed.append(path)
    return changed


def _normalise_execution(execution: dict | None, settings: Settings) -> dict:
    execution = dict(execution or {})
    return {
        "concurrency": _pick(
            execution.get("concurrency"), settings.eval_default_requested_concurrency
        ),
        "case_timeout_seconds": _pick(
            execution.get("case_timeout_seconds"), DEFAULT_CASE_TIMEOUT_SECONDS
        ),
        "max_attempts": _pick(execution.get("max_attempts"), DEFAULT_MAX_ATTEMPTS),
        "model_budget_cny": execution.get("model_budget_cny"),
        "shard_size": _pick(execution.get("shard_size"), settings.eval_shard_size_default),
        "execution_image_digest": execution.get("execution_image_digest"),
        "scoring_image_digest": execution.get("scoring_image_digest"),
    }


def build_preparation(
    session: Session,
    *,
    dataset_version_id: str,
    model_config_id: str,
    resource_pool_id: str,
    created_by: str,
    model_name: str | None = None,
    runtime_key: str = "claude-code",
    runtime_version: str | None = None,
    asset_snapshot_id: str | None = None,
    case_selection: dict | None = None,
    execution: dict | None = None,
    assets_resolved: bool = True,
    prompt_suffix: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict:
    """Resolve and freeze a launch configuration; return the prepare payload."""

    settings = settings or get_settings()
    now = _now(now)
    tenant_id = str(DEFAULT_TENANT_ID)
    execution = _normalise_execution(execution, settings)
    # M8-07: empty/whitespace-only instructions keep today's prompt behaviour.
    suffix = (prompt_suffix or "").strip()
    try:
        selection = _case_selection(case_selection)
    except PreparationInvalid as exc:
        selection = {"mode": "all", "count": None, "case_keys": None}
        selection_error: PreflightIssue | None = PreflightIssue(
            exc.code, str(exc), exc.field
        )
    else:
        selection_error = None

    result = check_preparation(
        session,
        dataset_version_id=dataset_version_id,
        model_config_id=model_config_id,
        model_name=model_name,
        runtime_key=runtime_key,
        runtime_version=runtime_version,
        resource_pool_id=resource_pool_id,
        requested_concurrency=int(execution["concurrency"]),
        scoring_image_digest=execution.get("scoring_image_digest"),
        execution_image_digest=execution.get("execution_image_digest"),
        assets_resolved=assets_resolved,
        asset_snapshot_id=asset_snapshot_id,
        case_timeout_seconds=int(execution["case_timeout_seconds"]),
        max_attempts=int(execution["max_attempts"]),
        model_budget_cny=(
            float(execution["model_budget_cny"])
            if execution.get("model_budget_cny") is not None
            else None
        ),
    )

    version = session.get(DatasetVersion, dataset_version_id)
    if version is None or version.tenant_id != tenant_id:
        version = None
    dataset = session.get(Dataset, version.dataset_id) if version is not None else None
    if dataset is not None and dataset.tenant_id != tenant_id:
        dataset = None

    case_keys: list[str] = []
    blockers: list[PreflightIssue] = list(result.blockers)
    if selection_error is not None:
        blockers.append(selection_error)
    elif version is not None:
        try:
            case_keys = resolve_case_keys(session, version, selection, settings)
        except PreparationInvalid as exc:
            blockers.append(PreflightIssue(exc.code, str(exc), exc.field))

    warnings = [issue.as_dict() for issue in result.warnings]
    if blockers:
        return {
            "ready": False,
            "blockers": [issue.as_dict() for issue in blockers],
            "warnings": warnings,
            "effective_concurrency": result.effective_concurrency,
            "execution_image_digest": result.execution_image_digest,
            "scoring_image_digest": result.scoring_image_digest,
            "asset_snapshot": result.asset_snapshot,
            "preparation_id": None,
            "expires_at": None,
            "snapshot_digest": None,
            "case_count": len(case_keys),
            "estimated_job_count": 0,
            "resolved_commit": (result.asset_snapshot or {}).get("resolved_commit"),
            "case_selection": selection,
            "execution": execution,
            "prompt_suffix": suffix,
            "cost_estimate": None,
            "summary": None,
        }

    if version is None or dataset is None:  # preflight normally blocks first
        raise PreparationInvalid("数据集版本不可用", field="dataset_version_id")
    pool = session.get(ResourcePool, resource_pool_id)
    config = session.get(ModelConfig, model_config_id)
    # Use the shared id helper so ids match every other domain row.
    preparation_id = new_id()
    expires_at = now + timedelta(seconds=settings.eval_preparation_ttl_seconds)
    asset_snapshot = _snapshot_asset(result.asset_snapshot)
    adapter = get_runtime_adapter(runtime_key)

    cost_estimate = estimate_cost(
        pricing=(config.pricing_json if config is not None else {}) or {},
        case_count=len(case_keys),
        effective_concurrency=int(
            result.effective_concurrency or execution["concurrency"]
        ),
        pool=pool,
        settings=settings,
    )

    snapshot = {
        "preparation_id": preparation_id,
        "created_by": created_by,
        "created_at": _iso(now),
        "expires_at": _iso(expires_at),
        "dataset": {
            "id": version.id,
            "dataset_id": dataset.id,
            "name": dataset.name,
            "slug": dataset.slug,
            "adapter_key": dataset.adapter_key,
            "version": version.version,
            "version_status": version.status,
            "case_count": version.case_count,
            "source_revision": version.source_revision,
            "execution_image_digest": version.execution_image_digest,
            "scoring_image_digest": version.scoring_image_digest,
        },
        "case_selection": selection,
        "case_count": len(case_keys),
        "case_keys_digest": _digest(case_keys),
        "runtime": {
            "key": runtime_key,
            "version": adapter.runtime_version if adapter is not None else None,
        },
        "asset_snapshot": asset_snapshot,
        "model": {
            "config_id": config.id if config is not None else model_config_id,
            "display_name": config.display_name if config is not None else None,
            "provider": config.provider if config is not None else None,
            "model_name": config.model_name if config is not None else None,
        },
        "resource_pool": {
            "id": pool.id if pool is not None else resource_pool_id,
            "name": pool.name if pool is not None else None,
            "provider_key": pool.provider_key if pool is not None else None,
            "max_capacity": capacity.pool_max_capacity(pool),
            "online_runners": _online_runners(session, pool, settings),
            "price_cny": money_str(pool.price_cny) if pool is not None else None,
        },
        "execution": execution,
        "resolved": {
            "effective_concurrency": result.effective_concurrency,
            "execution_image_digest": result.execution_image_digest,
            "scoring_image_digest": result.scoring_image_digest,
            "estimated_job_count": len(case_keys),
        },
        "prompt_suffix": suffix,
        "warnings": warnings,
        "cost_estimate": cost_estimate,
    }
    snapshot["summary"] = {
        "dataset": snapshot["dataset"],
        "case_count": len(case_keys),
        "estimated_job_count": len(case_keys),
        "runtime": snapshot["runtime"],
        "asset_snapshot": asset_snapshot,
        "model": snapshot["model"],
        "resource_pool": snapshot["resource_pool"],
        "execution": execution,
        "prompt_suffix": suffix,
        "resolved": snapshot["resolved"],
        "cost_estimate": cost_estimate,
        "warnings": warnings,
    }
    identity = _live_identity(session, snapshot, settings)
    snapshot["identity"] = identity
    digest = _digest(identity)

    preparation = EvalPreparation(
        id=preparation_id,
        tenant_id=tenant_id,
        created_by=created_by[:128],
        status="active",
        expires_at=expires_at,
        snapshot_json=snapshot,
        snapshot_digest=digest,
    )
    session.add(preparation)
    session.commit()

    return {
        "ready": True,
        "blockers": [],
        "warnings": warnings,
        "effective_concurrency": result.effective_concurrency,
        "execution_image_digest": result.execution_image_digest,
        "scoring_image_digest": result.scoring_image_digest,
        "asset_snapshot": result.asset_snapshot,
        "preparation_id": preparation_id,
        "expires_at": _iso(expires_at),
        "snapshot_digest": digest,
        "case_count": len(case_keys),
        "estimated_job_count": len(case_keys),
        "resolved_commit": (asset_snapshot or {}).get("resolved_commit"),
        "case_selection": selection,
        "execution": execution,
        "prompt_suffix": suffix,
        "cost_estimate": cost_estimate,
        "summary": snapshot["summary"],
    }


def _online_runners(
    session: Session, pool: ResourcePool | None, settings: Settings
) -> int:
    if pool is None:
        return 0
    return capacity.pool_runner_capacity(session, pool, settings=settings)


def consume_preparation(
    session: Session,
    *,
    preparation_id: str,
    idempotency_key: str,
    created_by: str,
    name: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[EvalRun, bool]:
    """Create the run frozen by ``preparation_id``; replay-safe per key."""

    settings = settings or get_settings()
    now = _now(now)
    tenant_id = str(DEFAULT_TENANT_ID)
    preparation = session.get(EvalPreparation, preparation_id)
    if (
        preparation is None
        or preparation.tenant_id != tenant_id
        or preparation.created_by != created_by[:128]
    ):
        raise PreparationNotFound("准备快照不存在或不属于当前用户")

    if preparation.status == "consumed":
        existing = (
            session.get(EvalRun, preparation.consumed_eval_run_id)
            if preparation.consumed_eval_run_id
            else None
        )
        if existing is not None and existing.idempotency_key == idempotency_key[:128]:
            return existing, False
        raise PreparationConsumed("该准备快照已用于创建评测，请重新准备")

    if preparation.status == "expired" or _as_utc(preparation.expires_at) <= now:
        preparation.status = "expired"
        session.commit()
        raise PreparationExpired("准备快照已过期，请重新确认配置")

    snapshot = preparation.snapshot_json or {}
    live = _live_identity(session, snapshot, settings)
    if _digest(live) != preparation.snapshot_digest:
        changed = _diff(snapshot.get("identity") or {}, live) or ["configuration"]
        raise ConfigurationChanged(changed)

    dataset = snapshot.get("dataset") or {}
    execution = snapshot.get("execution") or {}
    version = session.get(DatasetVersion, dataset.get("id"))
    if version is None or version.tenant_id != tenant_id:
        raise ConfigurationChanged(["dataset"])
    try:
        case_keys = resolve_case_keys(
            session, version, snapshot.get("case_selection") or {}, settings
        )
    except PreparationInvalid as exc:
        raise ConfigurationChanged([exc.field or "case_selection"]) from exc
    if _digest(case_keys) != snapshot.get("case_keys_digest"):
        raise ConfigurationChanged(["case_selection"])

    check = check_preparation(
        session,
        dataset_version_id=dataset.get("id") or "",
        model_config_id=(snapshot.get("model") or {}).get("config_id") or "",
        model_name=(snapshot.get("model") or {}).get("model_name"),
        runtime_key=(snapshot.get("runtime") or {}).get("key") or "claude-code",
        resource_pool_id=(snapshot.get("resource_pool") or {}).get("id") or "",
        requested_concurrency=int(execution.get("concurrency") or 1),
        scoring_image_digest=execution.get("scoring_image_digest"),
        execution_image_digest=execution.get("execution_image_digest"),
        assets_resolved=True,
        asset_snapshot_id=(snapshot.get("asset_snapshot") or {}).get("id"),
        case_timeout_seconds=execution.get("case_timeout_seconds"),
        max_attempts=execution.get("max_attempts"),
        model_budget_cny=(
            float(execution["model_budget_cny"])
            if execution.get("model_budget_cny") is not None
            else None
        ),
    )
    if check.blockers:
        raise PreparationBlocked([issue.as_dict() for issue in check.blockers])

    resolved = snapshot.get("resolved") or {}
    asset_snapshot = snapshot.get("asset_snapshot") or None
    run, created = evaluation_service.create_synthetic_evaluation(
        session,
        name=name or f"评测 · {dataset.get('name') or '数据集'} · {dataset.get('version') or ''}".strip(),
        case_keys=[],
        dataset_version_id=dataset.get("id"),
        selected_case_keys=case_keys,
        shard_size=execution.get("shard_size"),
        requested_concurrency=execution.get("concurrency"),
        case_timeout_seconds=execution.get("case_timeout_seconds"),
        max_attempts=execution.get("max_attempts"),
        idempotency_key=idempotency_key,
        created_by=created_by,
        model_config_id=(snapshot.get("model") or {}).get("config_id"),
        model_name=(snapshot.get("model") or {}).get("model_name"),
        model_budget_cny=execution.get("model_budget_cny"),
        runtime_key=(snapshot.get("runtime") or {}).get("key"),
        runtime_version=(snapshot.get("runtime") or {}).get("version"),
        resource_pool_id=(snapshot.get("resource_pool") or {}).get("id"),
        scoring_image_digest=resolved.get("scoring_image_digest"),
        execution_image_digest=resolved.get("execution_image_digest"),
        asset_snapshot_id=(asset_snapshot or {}).get("id"),
        asset_snapshot_json=asset_snapshot,
        prompt_suffix=snapshot.get("prompt_suffix") or "",
        consistency_json={
            "preparation_id": preparation.id,
            "snapshot_digest": preparation.snapshot_digest,
            "prepared_at": snapshot.get("created_at"),
            "expires_at": snapshot.get("expires_at"),
            "summary": snapshot.get("summary"),
            "warnings": snapshot.get("warnings", []),
            "blockers": [],
        },
        settings=settings,
    )

    preparation.status = "consumed"
    preparation.consumed_eval_run_id = run.id
    session.commit()
    return run, created

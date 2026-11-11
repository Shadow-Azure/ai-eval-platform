"""Result baseline recommendation and configuration comparison (M5-06).

The results page can only trust a delta when both runs cover the same case
population and the same metric definition version. This module recommends the
newest final run that shares the dataset version, runtime and model (preferring
an identical agent asset snapshot), and returns per-metric deltas together with
an explicit configuration diff. Manual baselines are allowed across
configurations, but every incomparable field is labelled instead of silently
mixing denominators.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Dataset,
    DatasetVersion,
    EvalCaseRun,
    EvalRun,
    MetricDefinition,
    MetricResult,
    ModelConfig,
    ResourcePool,
)
from . import evaluations

TERMINAL_STATUSES = ("completed", "partial", "failed", "cancelled")
KNOWN_DISPLAY_KINDS = ("number", "percentage", "ms", "cny", "histogram")

# The strict recommendation tier matches dataset version, runtime, model and
# the agent asset snapshot; the relaxed tier still requires the first three.
TIER_ASSETS = "same_dataset_runtime_model_assets"
TIER_RELAXED = "same_dataset_runtime_model"

CANDIDATE_LIMIT = 20

METRIC_ORDER = {
    "resolved_rate": 0,
    "resolved_count": 1,
    "resolved_rate_scored": 2,
    "execution_failure_rate": 3,
    "tokens_per_case": 4,
    "cost_per_case": 5,
    "duration_percentiles": 6,
}


class ComparisonInvalid(Exception):
    """A requested baseline cannot be compared (bad id, no results, self)."""

    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return evaluations.iso_utc(value)


def _metric_value(row: MetricResult) -> Any:
    value_json = row.value_json if isinstance(row.value_json, dict) else {}
    return value_json.get("value")


def _decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize() if value == value else value
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _short(value: Any, length: int = 12) -> str:
    if not isinstance(value, str) or not value:
        return "—"
    return value[:length]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _case_keys(session: Session, run_id: str) -> list[str]:
    return list(
        session.scalars(
            select(EvalCaseRun.case_key).where(EvalCaseRun.eval_run_id == run_id)
        )
    )


def _case_digest(keys: list[str]) -> str:
    return sha256("\n".join(sorted(keys)).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Metric selection
# ---------------------------------------------------------------------------


def _metric_rows(
    session: Session, run_id: str
) -> list[tuple[MetricResult, MetricDefinition]]:
    return list(
        session.execute(
            select(MetricResult, MetricDefinition)
            .join(
                MetricDefinition,
                MetricDefinition.id == MetricResult.metric_definition_id,
            )
            .where(MetricResult.eval_run_id == run_id)
            .order_by(MetricDefinition.metric_key, MetricResult.metric_version)
        )
    )


def _select_metric_rows(
    session: Session, run: EvalRun
) -> tuple[list[tuple[MetricResult, MetricDefinition]], bool]:
    """Final rows win; a running run falls back to its latest snapshot rows."""

    rows = _metric_rows(session, run.id)
    final_rows = [row for row in rows if row[0].is_final]
    if final_rows:
        return final_rows, True
    return rows, False


def _final_metric_rows(
    session: Session, run: EvalRun
) -> list[tuple[MetricResult, MetricDefinition]]:
    return [row for row in _metric_rows(session, run.id) if row[0].is_final]


def _metric_signature(
    rows: list[tuple[MetricResult, MetricDefinition]]
) -> str:
    return ",".join(
        sorted(f"{row.metric_key}@v{row.metric_version}" for row, _definition in rows)
    )


# ---------------------------------------------------------------------------
# Configuration facts and diff
# ---------------------------------------------------------------------------


def _dataset_facts(
    session: Session, run: EvalRun
) -> tuple[str, str]:
    if not run.dataset_version_id:
        return "合成评测", ""
    version = session.get(DatasetVersion, run.dataset_version_id)
    if version is None:
        return "数据集版本不可用", str(run.dataset_version_id)
    dataset = session.get(Dataset, version.dataset_id)
    name = dataset.name if dataset is not None else version.dataset_id
    return f"{name} · {version.version}", str(run.dataset_version_id)


def _model_facts(session: Session, run: EvalRun) -> tuple[str, str]:
    if run.model_config_id:
        config = session.get(ModelConfig, run.model_config_id)
        if config is not None:
            return (
                f"{config.display_name}（{config.model_name}）",
                f"config:{config.id}",
            )
    if run.model_name:
        return run.model_name, f"name:{run.model_name}"
    return "未绑定模型", ""


def _pool_facts(session: Session, run: EvalRun) -> tuple[str, str]:
    if not run.resource_pool_id:
        return "—", ""
    pool = session.get(ResourcePool, run.resource_pool_id)
    if pool is None:
        return "资源池不可用", str(run.resource_pool_id)
    return f"{pool.name} · {pool.provider_key}", str(pool.id)


def _runtime_key(run: EvalRun) -> str:
    return f"{run.runtime_key or ''}@{run.runtime_version or ''}"


def _assets_key(run: EvalRun) -> str:
    assets = run.asset_snapshot_json or {}
    return f"{assets.get('resolved_commit') or ''}|{assets.get('tree_digest') or ''}"


def _config_facts(
    session: Session,
    run: EvalRun,
    metric_rows: list[tuple[MetricResult, MetricDefinition]],
    case_keys: list[str],
) -> list[dict[str, Any]]:
    dataset_label, dataset_key = _dataset_facts(session, run)
    model_label, model_key = _model_facts(session, run)
    pool_label, pool_key = _pool_facts(session, run)
    assets = run.asset_snapshot_json or {}
    metric_label = (
        "、".join(
            f"{row.metric_key} v{row.metric_version}" for row, _definition in metric_rows
        )
        or "—"
    )
    return [
        {
            "key": "dataset_version",
            "label": "数据集版本",
            "category": "数据",
            "display": dataset_label,
            "compare": dataset_key,
            "note": "数据集版本不同；指标是否可比仍以用例集合与指标版本判定",
        },
        {
            "key": "case_set",
            "label": "用例集合",
            "category": "数据",
            "display": f"{len(case_keys)} 个用例",
            "compare": _case_digest(case_keys),
            "note": "用例集合不同，分母与总体不同，指标差值不可直接比较",
        },
        {
            "key": "metric_versions",
            "label": "指标口径",
            "category": "数据",
            "display": metric_label,
            "compare": _metric_signature(metric_rows),
            "note": "指标口径版本不同，仅展示数值，不计算差值",
        },
        {
            "key": "runtime",
            "label": "Runtime",
            "category": "执行",
            "display": f"{run.runtime_key or '—'} {run.runtime_version or ''}".strip(),
            "compare": _runtime_key(run),
            "note": "Runtime 不同，结果差异可能来自执行环境",
        },
        {
            "key": "model",
            "label": "模型",
            "category": "执行",
            "display": model_label,
            "compare": model_key,
            "note": "模型不同，结果差异可能来自模型能力",
        },
        {
            "key": "asset_commit",
            "label": "Agent 资产 commit",
            "category": "执行",
            "display": _short(assets.get("resolved_commit")),
            "compare": str(assets.get("resolved_commit") or ""),
            "note": "Agent 资产 commit 不同，结果差异可能来自资产内容",
        },
        {
            "key": "asset_tree",
            "label": "资产树摘要",
            "category": "执行",
            "display": _short(assets.get("tree_digest")),
            "compare": str(assets.get("tree_digest") or ""),
            "note": "资产树摘要不同，结果差异可能来自资产内容",
        },
        {
            "key": "resource_pool",
            "label": "资源池",
            "category": "执行",
            "display": pool_label,
            "compare": pool_key,
            "note": "资源池不同，成本与排队状态可能不同",
        },
        {
            "key": "concurrency",
            "label": "请求并发",
            "category": "策略",
            "display": f"{run.requested_concurrency}",
            "compare": str(run.requested_concurrency),
            "note": "并发不同，单项耗时与成本可能不同",
        },
        {
            "key": "timeout",
            "label": "单用例超时",
            "category": "策略",
            "display": f"{run.case_timeout_seconds} 秒",
            "compare": str(run.case_timeout_seconds),
            "note": "超时不同，失败/超时分布可能不同",
        },
        {
            "key": "max_attempts",
            "label": "失败重试",
            "category": "策略",
            "display": f"{run.max_attempts} 次",
            "compare": str(run.max_attempts),
            "note": "重试次数不同，最终结果可能不同",
        },
        {
            "key": "shard_size",
            "label": "分片大小",
            "category": "策略",
            "display": f"{run.shard_size} 用例 / 次",
            "compare": str(run.shard_size),
            "note": "分片大小不同，调度与耗时可能不同",
        },
        {
            "key": "execution_image",
            "label": "执行镜像 digest",
            "category": "镜像",
            "display": _short(run.execution_image_digest),
            "compare": str(run.execution_image_digest or ""),
            "note": "执行镜像不同，环境行为可能不同",
        },
        {
            "key": "scoring_image",
            "label": "评分镜像 digest",
            "category": "镜像",
            "display": _short(run.scoring_image_digest),
            "compare": str(run.scoring_image_digest or ""),
            "note": "评分镜像不同，评分口径实现可能不同",
        },
    ]


INCOMPARABLE_FACT_KEYS = {"case_set", "metric_versions"}


def _config_differences(
    current_facts: list[dict[str, Any]], baseline_facts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    baseline_by_key = {fact["key"]: fact for fact in baseline_facts}
    rows: list[dict[str, Any]] = []
    case_set_same = True
    for fact in current_facts:
        other = baseline_by_key.get(fact["key"])
        if other is None:  # pragma: no cover - the fact list is fixed
            continue
        changed = fact["compare"] != other["compare"]
        incomparable = changed and fact["key"] in INCOMPARABLE_FACT_KEYS
        if fact["key"] == "case_set" and changed:
            case_set_same = False
        rows.append(
            {
                "key": fact["key"],
                "label": fact["label"],
                "category": fact["category"],
                "current": fact["display"],
                "baseline": other["display"],
                "changed": changed,
                "incomparable": incomparable,
                "note": fact["note"] if changed else None,
            }
        )
    return rows, case_set_same


# ---------------------------------------------------------------------------
# Metric deltas
# ---------------------------------------------------------------------------


def _same_denominator(
    current: Decimal | None, baseline: Decimal | None
) -> bool:
    if current is None and baseline is None:
        return True
    if current is None or baseline is None:
        return False
    return current == baseline


def _delta(
    current_value: Any, baseline_value: Any
) -> tuple[Any, Any]:
    """Return (delta, delta_ratio) following the metric's display shape."""

    if _is_number(current_value) and _is_number(baseline_value):
        delta = float(current_value) - float(baseline_value)
        ratio = delta / float(baseline_value) if float(baseline_value) != 0 else None
        return delta, ratio
    if isinstance(current_value, dict) and isinstance(baseline_value, dict):
        delta: dict[str, float] = {}
        ratio: dict[str, float] = {}
        for key in sorted(set(current_value) & set(baseline_value)):
            current_item = current_value[key]
            baseline_item = baseline_value[key]
            if not (_is_number(current_item) and _is_number(baseline_item)):
                continue
            item_delta = float(current_item) - float(baseline_item)
            delta[key] = item_delta
            if float(baseline_item) != 0:
                ratio[key] = item_delta / float(baseline_item)
        if not delta:
            return None, None
        return delta, ratio or None
    return None, None


def _improvement(delta: Any, direction: str) -> Any:
    sign = 1 if direction != "lower_is_better" else -1
    if isinstance(delta, dict):
        return {
            key: (None if value == 0 else sign * value > 0)
            for key, value in delta.items()
        }
    if _is_number(delta):
        return None if delta == 0 else sign * delta > 0
    return None


def _metric_deltas(
    current_rows: list[tuple[MetricResult, MetricDefinition]],
    baseline_rows: list[tuple[MetricResult, MetricDefinition]],
    *,
    case_set_same: bool,
    current_is_final: bool,
    baseline_is_final: bool,
) -> list[dict[str, Any]]:
    current_by_key = {row.metric_key: (row, definition) for row, definition in current_rows}
    baseline_by_key = {row.metric_key: (row, definition) for row, definition in baseline_rows}
    keys = sorted(
        set(current_by_key) | set(baseline_by_key),
        key=lambda key: (METRIC_ORDER.get(key, 100), key),
    )
    items: list[dict[str, Any]] = []
    for key in keys:
        current = current_by_key.get(key)
        baseline = baseline_by_key.get(key)
        definition = (current or baseline)[1]
        current_value = _metric_value(current[0]) if current else None
        baseline_value = _metric_value(baseline[0]) if baseline else None
        comparable = True
        reason: str | None = None

        def block(message: str) -> None:
            nonlocal comparable, reason
            comparable = False
            if reason is None:
                reason = message

        if current is None:
            block("当前评测缺少该指标")
        elif baseline is None:
            block("基线评测缺少该指标")
        else:
            if current[0].metric_version != baseline[0].metric_version:
                block(
                    f"指标版本不同（当前 v{current[0].metric_version}，"
                    f"基线 v{baseline[0].metric_version}）"
                )
            if current[1].denominator_policy != baseline[1].denominator_policy:
                block(
                    "分母口径不同（"
                    f"{current[1].denominator_policy or '—'} vs "
                    f"{baseline[1].denominator_policy or '—'}）"
                )
            if not _same_denominator(current[0].denominator, baseline[0].denominator):
                block(
                    "分母不同（"
                    f"{_decimal_str(current[0].denominator) or '—'} vs "
                    f"{_decimal_str(baseline[0].denominator) or '—'}）"
                )
            if not case_set_same:
                block("用例集合不同，指标总体不可直接比较")

        display_kind = definition.display_kind
        if display_kind not in KNOWN_DISPLAY_KINDS:
            block(f"未知指标类型 {display_kind}，仅展示原始值")

        delta = None
        delta_ratio = None
        improvement = None
        if comparable:
            if current_value is None or baseline_value is None:
                block("指标值缺失，无法计算差值")
            else:
                delta, delta_ratio = _delta(current_value, baseline_value)
                if delta is None:
                    block("指标值结构不同，无法计算差值")
                else:
                    improvement = _improvement(delta, definition.direction)

        items.append(
            {
                "metric_key": key,
                "metric_version": current[0].metric_version if current else None,
                "baseline_metric_version": baseline[0].metric_version if baseline else None,
                "display_name": definition.display_name,
                "description": definition.description,
                "formula_text": definition.formula_text,
                "display_kind": display_kind,
                "unit": definition.unit,
                "direction": definition.direction,
                "denominator_policy": definition.denominator_policy,
                "current_value": current_value,
                "baseline_value": baseline_value,
                "current_numerator": _decimal_str(current[0].numerator) if current else None,
                "current_denominator": _decimal_str(current[0].denominator) if current else None,
                "baseline_numerator": _decimal_str(baseline[0].numerator) if baseline else None,
                "baseline_denominator": _decimal_str(baseline[0].denominator) if baseline else None,
                "delta": delta,
                "delta_ratio": delta_ratio,
                "improvement": improvement,
                "comparable": comparable,
                "incomparable_reason": reason,
                "current_is_final": current_is_final,
                "baseline_is_final": baseline_is_final,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Candidates and recommendation
# ---------------------------------------------------------------------------


def _candidate_differences(
    session: Session,
    current: EvalRun,
    candidate: EvalRun,
    *,
    current_digest: str,
    candidate_digest: str,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    comparable = current_digest == candidate_digest
    if not comparable:
        reasons.append("用例集合不同")
    if (current.dataset_version_id or "") != (candidate.dataset_version_id or ""):
        reasons.append("数据集版本不同")
    if _runtime_key(current) != _runtime_key(candidate):
        reasons.append("Runtime 不同")
    if (_model_facts(session, current)[1]) != (_model_facts(session, candidate)[1]):
        reasons.append("模型不同")
    if _assets_key(current) != _assets_key(candidate):
        reasons.append("资产快照不同")
    if (current.resource_pool_id or "") != (candidate.resource_pool_id or ""):
        reasons.append("资源池不同")
    return comparable, reasons


def _candidate_runs(
    session: Session, current: EvalRun, *, current_digest: str
) -> list[dict[str, Any]]:
    # Filter on the EXISTS subquery, not after the LIMIT: terminal runs without
    # final metrics (synthetic or dataset-less runs) would otherwise fill the
    # window and hide an older eligible baseline.
    has_final_metric = (
        select(MetricResult.id)
        .where(
            MetricResult.eval_run_id == EvalRun.id,
            MetricResult.is_final.is_(True),
        )
        .exists()
    )
    statement = (
        select(EvalRun)
        .where(
            EvalRun.tenant_id == current.tenant_id,
            EvalRun.id != current.id,
            EvalRun.status.in_(TERMINAL_STATUSES),
            has_final_metric,
        )
        .order_by(
            func.coalesce(EvalRun.finished_at, EvalRun.created_at).desc(),
            EvalRun.created_at.desc(),
        )
        .limit(CANDIDATE_LIMIT)
    )
    candidates: list[dict[str, Any]] = []
    for run in session.scalars(statement):
        rows = _final_metric_rows(session, run)
        keys = _case_keys(session, run.id)
        digest = _case_digest(keys)
        comparable, reasons = _candidate_differences(
            session,
            current,
            run,
            current_digest=current_digest,
            candidate_digest=digest,
        )
        candidates.append(
            {
                "run": run,
                "rows": rows,
                "digest": digest,
                "comparable": comparable,
                "reasons": reasons,
                "public": {
                    "id": run.id,
                    "name": run.name,
                    "status": run.status,
                    "created_at": _iso(run.created_at),
                    "finished_at": _iso(run.finished_at),
                    "dataset_version_id": run.dataset_version_id,
                    "dataset_label": _dataset_facts(session, run)[0],
                    "runtime_key": run.runtime_key,
                    "runtime_version": run.runtime_version,
                    "model_name": _model_facts(session, run)[0],
                    "asset_resolved_commit": (run.asset_snapshot_json or {}).get(
                        "resolved_commit"
                    ),
                    "case_count": len(keys),
                    "comparable": comparable,
                    "reasons": reasons,
                },
            }
        )
        if len(candidates) >= CANDIDATE_LIMIT:
            break
    return candidates


def _recommend(
    session: Session, current: EvalRun, candidates: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    for tier in (TIER_ASSETS, TIER_RELAXED):
        for candidate in candidates:
            run = candidate["run"]
            if not candidate["comparable"]:
                continue
            if (run.dataset_version_id or "") != (current.dataset_version_id or ""):
                continue
            if _runtime_key(run) != _runtime_key(current):
                continue
            if _model_facts(session, run)[1] != _model_facts(session, current)[1]:
                continue
            if tier == TIER_ASSETS and _assets_key(run) != _assets_key(current):
                continue
            reason = (
                "同数据集、同 Runtime、同模型与同资产快照的最近一次最终评测"
                if tier == TIER_ASSETS
                else "同数据集、同 Runtime、同模型的最近一次最终评测（资产快照不同）"
            )
            return candidate, tier, reason
    return None, None, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _baseline_summary(
    session: Session, run: EvalRun, digest: str
) -> dict[str, Any]:
    keys = _case_keys(session, run.id)
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "created_at": _iso(run.created_at),
        "finished_at": _iso(run.finished_at),
        "dataset_version_id": run.dataset_version_id,
        "dataset_label": _dataset_facts(session, run)[0],
        "runtime_key": run.runtime_key,
        "runtime_version": run.runtime_version,
        "model_name": _model_facts(session, run)[0],
        "asset_resolved_commit": (run.asset_snapshot_json or {}).get("resolved_commit"),
        "resource_pool_id": run.resource_pool_id,
        "case_count": len(keys),
        "case_digest": digest,
    }


def compare_run(
    session: Session,
    run: EvalRun,
    *,
    tenant_id: str,
    baseline_run_id: str | None = None,
) -> dict[str, Any]:
    """Recommend or load a baseline and return deltas plus a config diff."""

    if run.tenant_id != tenant_id:
        raise ComparisonInvalid(
            "评测不存在", code="eval_run_not_found", status_code=404
        )

    current_rows, current_is_final = _select_metric_rows(session, run)
    current_keys = _case_keys(session, run.id)
    current_digest = _case_digest(current_keys)
    current_facts = _config_facts(session, run, current_rows, current_keys)

    candidates = _candidate_runs(session, run, current_digest=current_digest)

    baseline: EvalRun | None = None
    baseline_rows: list[tuple[MetricResult, MetricDefinition]] = []
    baseline_keys: list[str] = []
    baseline_digest = ""
    recommendation = None
    manual = False

    if baseline_run_id:
        manual = True
        if baseline_run_id == run.id:
            raise ComparisonInvalid(
                "不能把当前评测作为自己的基线",
                code="baseline_run_self",
                status_code=400,
            )
        baseline = session.get(EvalRun, baseline_run_id)
        if baseline is None or baseline.tenant_id != tenant_id:
            raise ComparisonInvalid(
                "基线评测不存在", code="baseline_run_not_found", status_code=404
            )
        baseline_rows = _final_metric_rows(session, baseline)
        if not baseline_rows:
            raise ComparisonInvalid(
                "基线评测尚未产出最终结果，不能作为基线",
                code="baseline_results_unavailable",
                status_code=409,
            )
        baseline_keys = _case_keys(session, baseline.id)
        baseline_digest = _case_digest(baseline_keys)
    else:
        chosen, tier, reason = _recommend(session, run, candidates)
        if chosen is not None:
            baseline = chosen["run"]
            baseline_rows = chosen["rows"]
            baseline_digest = chosen["digest"]
            baseline_keys = _case_keys(session, baseline.id)
            recommendation = {"tier": tier, "reason": reason}

    empty_state = None
    if baseline is None:
        if not current_rows:
            empty_state = {
                "code": "current_results_unavailable",
                "message": "当前评测尚未产出结果指标，暂时无法对比。",
            }
        else:
            empty_state = {
                "code": "no_comparable_baseline",
                "message": (
                    "没有同数据集、同 Runtime、同模型的最终评测可作基线；"
                    "不会自动选择不可比评测。"
                ),
            }
        return {
            "run_id": run.id,
            "run_name": run.name,
            "status": run.status,
            "is_final": current_is_final,
            "manual": manual,
            "baseline_run_id": None,
            "recommendation": None,
            "baseline": None,
            "candidates": [candidate["public"] for candidate in candidates],
            "comparable": False,
            "incomparable_reasons": [],
            "case_set": {
                "comparable": False,
                "current_count": len(current_keys),
                "baseline_count": 0,
                "current_digest": current_digest,
                "baseline_digest": None,
            },
            "metric_deltas": [],
            "configuration_differences": [],
            "empty_state": empty_state,
            "generated_at": _iso(datetime.now(UTC)),
        }

    baseline_facts = _config_facts(session, baseline, baseline_rows, baseline_keys)
    configuration_differences, case_set_same = _config_differences(
        current_facts, baseline_facts
    )
    metric_deltas = _metric_deltas(
        current_rows,
        baseline_rows,
        case_set_same=case_set_same,
        current_is_final=current_is_final,
        baseline_is_final=True,
    )

    incomparable_reasons: list[str] = []
    if not case_set_same:
        incomparable_reasons.append(
            f"用例集合不同（当前 {len(current_keys)} 个，基线 {len(baseline_keys)} 个）"
        )

    return {
        "run_id": run.id,
        "run_name": run.name,
        "status": run.status,
        "is_final": current_is_final,
        "manual": manual,
        "baseline_run_id": baseline.id,
        "recommendation": recommendation,
        "baseline": _baseline_summary(session, baseline, baseline_digest),
        "candidates": [candidate["public"] for candidate in candidates],
        "comparable": case_set_same,
        "incomparable_reasons": incomparable_reasons,
        "case_set": {
            "comparable": case_set_same,
            "current_count": len(current_keys),
            "baseline_count": len(baseline_keys),
            "current_digest": current_digest,
            "baseline_digest": baseline_digest,
        },
        "metric_deltas": metric_deltas,
        "configuration_differences": configuration_differences,
        "empty_state": None,
        "generated_at": _iso(datetime.now(UTC)),
    }

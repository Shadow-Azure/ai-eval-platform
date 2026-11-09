"""Metric definition registry（M3-01，design 07 §3.1/§3.2，决策 D10）。

- 聚合器只能来自平台白名单：``ratio/sum/mean/percentile/histogram``。
  白名单外的 ``aggregator_key`` 在注册层直接拒绝（不执行），DB 层另有
  CHECK 兜底——库里不存也不执行任意代码。
- ``formula_text`` 只用于口径解释，永远不作为 Python/SQL 执行。
- 注册项版本化：同 ``(dataset, metric_key, version)`` 唯一；修正口径 =
  注册新 version，不改历史。
- 分子/分母只能引用注册的用例结果集合（07 §3.2）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..datasets import DatasetAdapter
from ..models import AGGREGATOR_WHITELIST, Dataset, MetricDefinition

# design 07 §3.2：分子引用的用例结果集合 / 分母引用的用例集合白名单。
CASE_RESULT_KEYS = ("resolved", "unresolved", "not_scored", "execution_failed")
CASE_SET_KEYS = ("selected_total", "submitted", "graded", "resolved", "execution_failed")

# 需要分母的聚合器；sum/mean 只看分子。
AGGREGATORS_REQUIRING_DENOMINATOR = {"ratio"}


class MetricInvalid(Exception):
    """指标注册契约违规（分类码见 ``code``）。"""

    def __init__(self, message: str, *, code: str, field: str | None = None) -> None:
        self.code = code
        self.field = field
        super().__init__(message)


def validate_metric_config(
    *,
    aggregator_key: str,
    numerator: dict[str, Any] | None,
    denominator: dict[str, Any] | None,
    display_kind: str,
    direction: str = "higher_is_better",
) -> None:
    if aggregator_key not in AGGREGATOR_WHITELIST:
        raise MetricInvalid(
            f"聚合器 {aggregator_key!r} 不在平台白名单内（{', '.join(AGGREGATOR_WHITELIST)}），"
            "新算法需随平台版本新增聚合器实现",
            code="aggregator_not_allowed",
            field="aggregator_key",
        )
    if display_kind not in ("number", "percentage", "histogram", "ms", "usd"):
        raise MetricInvalid(f"展示类型 {display_kind!r} 不受支持", code="display_kind_invalid", field="display_kind")
    if direction not in ("higher_is_better", "lower_is_better"):
        raise MetricInvalid(f"方向 {direction!r} 不受支持", code="direction_invalid", field="direction")
    if not isinstance(numerator, dict) or numerator.get("case_result") not in CASE_RESULT_KEYS:
        raise MetricInvalid(
            "分子必须引用注册的用例结果集合：" + ", ".join(CASE_RESULT_KEYS),
            code="case_result_not_registered",
            field="numerator",
        )
    if denominator is None:
        if aggregator_key in AGGREGATORS_REQUIRING_DENOMINATOR:
            raise MetricInvalid(f"聚合器 {aggregator_key} 需要分母", code="denominator_required", field="denominator")
        return
    if not isinstance(denominator, dict) or denominator.get("case_set") not in CASE_SET_KEYS:
        raise MetricInvalid(
            "分母必须引用注册的用例集合：" + ", ".join(CASE_SET_KEYS),
            code="case_set_not_registered",
            field="denominator",
        )


def register_metric_definition(
    session: Session,
    *,
    dataset_id: str,
    metric_key: str,
    version: int,
    aggregator_key: str,
    numerator: dict[str, Any] | None,
    denominator: dict[str, Any] | None,
    display_kind: str,
    display_name: str,
    description: str | None = None,
    formula_text: str | None = None,
    unit: str | None = None,
    direction: str = "higher_is_better",
    denominator_policy: str | None = None,
) -> MetricDefinition:
    """注册（或幂等复用）一个版本化指标定义；调用方拥有事务。"""

    validate_metric_config(
        aggregator_key=aggregator_key,
        numerator=numerator,
        denominator=denominator,
        display_kind=display_kind,
        direction=direction,
    )
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise MetricInvalid(f"数据集不存在：{dataset_id}", code="dataset_not_found", field="dataset_id")
    existing = session.scalar(
        select(MetricDefinition).where(
            MetricDefinition.dataset_id == dataset_id,
            MetricDefinition.metric_key == metric_key,
            MetricDefinition.version == version,
        )
    )
    config = {
        "numerator": numerator,
        "denominator": denominator,
    }
    if existing is not None:
        same = (
            existing.aggregator_key == aggregator_key
            and existing.config_json.get("numerator") == numerator
            and existing.config_json.get("denominator") == denominator
        )
        if not same:
            raise MetricInvalid(
                f"指标 {metric_key} v{version} 已存在且口径不同：修正口径请新增 version",
                code="metric_version_conflict",
                field="version",
            )
        return existing
    definition = MetricDefinition(
        tenant_id=dataset.tenant_id,
        dataset_id=dataset.id,
        metric_key=metric_key,
        version=version,
        display_name=display_name,
        description=description,
        aggregator_key=aggregator_key,
        formula_text=formula_text,
        display_kind=display_kind,
        unit=unit,
        direction=direction,
        denominator_policy=denominator_policy,
        config_json=config,
    )
    session.add(definition)
    session.flush()
    return definition


def register_adapter_metrics(
    session: Session,
    *,
    dataset: Dataset,
    adapter: DatasetAdapter,
) -> list[MetricDefinition]:
    """按适配器声明的指标配置注册整个家族的指标（幂等）。"""

    definitions: list[MetricDefinition] = []
    for metric_key in adapter.metric_keys():
        config = adapter.metric_configs()[metric_key]
        definitions.append(
            register_metric_definition(
                session,
                dataset_id=dataset.id,
                metric_key=metric_key,
                version=config.get("version", 1),
                aggregator_key=config["aggregator_key"],
                numerator=config.get("numerator"),
                denominator=config.get("denominator"),
                display_kind=config["display_kind"],
                display_name=config["display_name"],
                description=config.get("description"),
                formula_text=config.get("formula_text"),
                unit=config.get("unit"),
                direction=config.get("direction", "higher_is_better"),
                denominator_policy=config.get("denominator_policy"),
            )
        )
    return definitions

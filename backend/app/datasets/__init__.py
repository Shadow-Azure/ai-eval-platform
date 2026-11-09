"""Dataset adapter contracts (design 07 §1).

每个数据集家族实现一个适配器并注册到平台注册表；后续数据集只加适配器，
不改导入/查询流程。M3-01 交付导入面的契约（校验 + 标准映射 + 指标键）；
``build_task`` / ``score_parser`` 属于执行/评分里程碑（M3-02+），暂缓。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .registry import (
    AdapterNotFound,
    get_adapter,
    register_adapter,
    registered_adapter_keys,
    unregister_adapter,
)

# 校验失败分类（验收 4：可诊断错误）。
ISSUE_CODES = (
    "missing_field",
    "bad_patch",
    "invalid_field_format",
    "duplicate_case_key",
    "adapter_not_found",
    "version_conflict",
)


@dataclass
class CaseIssue:
    """一条可诊断的校验失败：分类码 + 字段 + 人话说明 + 定位。"""

    code: str
    message: str
    field: str | None = None
    case_index: int | None = None
    case_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "field": self.field,
            "message": self.message,
            "case_index": self.case_index,
            "case_key": self.case_key,
        }


@dataclass
class NormalizedCase:
    """适配器输出的平台标准字段（design 07 §1.1 标准映射）。"""

    case_key: str
    problem_statement: str
    repo: str | None = None
    base_commit: str | None = None
    reference_patch: str | None = None
    test_patch: str | None = None
    expected_metrics: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DatasetAdapter(Protocol):
    """M3-01 导入面适配器契约。

    - ``key`` / ``adapter_version`` 随版本冻结，重复导入靠它们判幂等。
    - ``validate_case`` 返回 ``CaseIssue`` 列表（空列表 = 合法）。
    - ``normalize_case`` 只对通过校验的记录调用。
    - ``metric_configs`` 声明该数据集家族的指标注册配置（版本化）。
    """

    key: str
    adapter_version: str

    def validate_case(self, raw: Mapping[str, Any]) -> list[CaseIssue]: ...

    def normalize_case(self, raw: Mapping[str, Any]) -> NormalizedCase: ...

    def metric_keys(self) -> list[str]: ...

    def metric_configs(self) -> dict[str, dict[str, Any]]: ...

    def case_visibility(self) -> dict[str, str]:
        """字段可见性分类：agent（题面）/ scoring_only（参考与测试材料）。"""
        ...


__all__ = [
    "AdapterNotFound",
    "CaseIssue",
    "DatasetAdapter",
    "ISSUE_CODES",
    "NormalizedCase",
    "get_adapter",
    "register_adapter",
    "registered_adapter_keys",
    "unregister_adapter",
]

# 平台内置适配器：新数据集家族在此追加一行注册（或由扩展包注册）。
from .swebench import SWEBenchAdapter  # noqa: E402

register_adapter(SWEBenchAdapter())

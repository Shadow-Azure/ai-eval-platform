"""SWE-bench dataset adapter（design 07 §1.1 标准映射 + §3.2 官方口径）。

上游 ``princeton-nlp/SWE-bench_Verified`` 行字段 → 平台字段：

- ``instance_id``            → ``dataset_case.case_key``（版本内唯一）
- ``repo`` / ``base_commit`` → 标准字段（固定代码基线）
- ``problem_statement``      → Agent 可见题面
- ``patch``                  → 参考补丁（仅评分自检，Agent 不可见）
- ``test_patch``             → 测试补丁（Agent 执行阶段不可读）
- ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` → expected_metrics（评分输入）
- 其余字段                   → ``extra_json``（保留，不参与核心关联）

主指标固定为官方口径 ``% Resolved = resolved / total``：分母是本次选中的
全部用例，error / 空补丁 / 未完成 / 未解决都不从分母剔除（07 §3.2）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from . import CaseIssue, NormalizedCase

DIFF_PREFIX = "diff --git"

TEST_SET_FIELDS = ("FAIL_TO_PASS", "PASS_TO_PASS")
CORE_FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "patch",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
)

# SWE-bench 官方口径指标（版本化注册项；07 §3.2 指标表）。
METRIC_CONFIGS: dict[str, dict[str, Any]] = {
    "resolved_rate": {
        "version": 1,
        "aggregator_key": "ratio",
        "numerator": {"case_result": "resolved"},
        "denominator": {"case_set": "selected_total"},
        "display_kind": "percentage",
        "display_name": "解决率（% Resolved）",
        "description": "SWE-bench 官方主口径：榜单可比的主结果卡。",
        "formula_text": (
            "% Resolved = resolved / total_instances；total 为本次选中的全部"
            "数据集用例，error、空补丁、未完成与未解决都不从分母剔除。"
        ),
        "denominator_policy": "selected_total",
    },
    "resolved_count": {
        "version": 1,
        "aggregator_key": "sum",
        "numerator": {"case_result": "resolved"},
        "denominator": None,
        "display_kind": "number",
        "display_name": "Resolved 数",
        "description": "最终有效 attempt 中 resolved 的逻辑用例数。",
        "formula_text": "resolved_count = Σ 1(用例结果为 resolved)",
    },
    "resolved_rate_scored": {
        "version": 1,
        "aggregator_key": "ratio",
        "numerator": {"case_result": "resolved"},
        "denominator": {"case_set": "graded"},
        "display_kind": "percentage",
        "display_name": "解决率（已评分口径）",
        "description": "辅助诊断指标，不能取代榜单可比的主指标。",
        "formula_text": ("resolved_rate_scored = resolved / (resolved + unresolved)；无已评分用例时为 N/A。"),
        "denominator_policy": "graded",
    },
    "execution_failure_rate": {
        "version": 1,
        "aggregator_key": "ratio",
        "numerator": {"case_result": "execution_failed"},
        "denominator": {"case_set": "selected_total"},
        "display_kind": "percentage",
        "display_name": "执行失败率",
        "description": "质量告警：基础设施失败分类是附加信息，不从总数扣除。",
        "formula_text": ("execution_failure_rate = 执行失败/超时/取消用例数 / total_instances"),
    },
    # M8-53 (#332): token/cost cards. Values come from the trace tree, not from
    # a MetricResult row: the usage definition is registered so new dataset
    # versions expose the card in 指标口径, while /results always computes the
    # live value (traces arrive after the completion transaction).
    "total_tokens": {
        "version": 1,
        "aggregator_key": "sum",
        "numerator": None,
        "denominator": None,
        "display_kind": "number",
        "unit": "tokens",
        "direction": "lower_is_better",
        "display_name": "总 Token",
        "description": "每个用例所有 attempt 的 input_tokens + output_tokens 合计。",
        "formula_text": (
            "total_tokens = Σ(每个用例所有 attempt 的 input_tokens + output_tokens)；"
            "AGENT 汇总节点不计入，缓存读写单独展示。"
        ),
    },
    "tokens_per_case": {
        "version": 1,
        "aggregator_key": "mean",
        "numerator": None,
        "denominator": None,
        "display_kind": "number",
        "unit": "tokens",
        "direction": "lower_is_better",
        "display_name": "平均 Token / 用例",
        "description": "按有轨迹数据的用例数取平均。",
        "formula_text": (
            "tokens_per_case = total_tokens / cases_with_tokens；"
            "cases_with_tokens 为至少有一个已解析 attempt 轨迹的用例数。"
        ),
        "denominator_policy": "cases_with_tokens",
    },
    "total_cost_usd": {
        "version": 1,
        "aggregator_key": "sum",
        "numerator": None,
        "denominator": None,
        "display_kind": "number",
        "unit": "USD",
        "direction": "lower_is_better",
        "display_name": "累计成本（USD）",
        "description": "trace cost-state 上报的成本，单位 USD。",
        "formula_text": "total_cost_usd = Σ 所有 attempt 的 TraceSpan.cost（USD）",
    },
    "cost_per_case_usd": {
        "version": 1,
        "aggregator_key": "mean",
        "numerator": None,
        "denominator": None,
        "display_kind": "number",
        "unit": "USD",
        "direction": "lower_is_better",
        "display_name": "平均成本（USD / 用例）",
        "description": "按有轨迹数据的用例数取平均，单位 USD。",
        "formula_text": "cost_per_case_usd = total_cost_usd / cases_with_tokens",
        "denominator_policy": "cases_with_tokens",
    },
}


def _validate_test_set(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return "必须是 JSON 数组字符串或字符串数组"
    else:
        decoded = value
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        return "必须是字符串数组"
    return None


def _as_test_set(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        decoded = json.loads(value)
    else:
        decoded = value
    return list(decoded)


class SWEBenchAdapter:
    """SWE-bench（Verified 等 split）→ 平台任务视图的适配器。"""

    key = "swebench"
    adapter_version = "1"
    display_name = "SWE-bench Adapter"
    task_family = "软件工程"
    environment_kind = "GitHub Issue · Docker"
    description = "经人工确认的 GitHub Issue 修复任务，适合作为发版验收集。"
    primary_metric_key = "resolved_rate"

    REQUIRED_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement", "patch")
    # M5-15: field completeness in the upload validation report iterates this
    # platform-facing alias (the uppercase tuple predates the report).
    required_fields = REQUIRED_FIELDS

    # -- validation ---------------------------------------------------------

    def validate_case(self, raw: Mapping[str, Any]) -> list[CaseIssue]:
        issues: list[CaseIssue] = []
        for field in self.REQUIRED_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    CaseIssue(
                        code="missing_field",
                        field=field,
                        message=f"缺少必填字段 {field}",
                    )
                )
        for field in ("patch", "test_patch"):
            value = raw.get(field)
            if isinstance(value, str) and value.strip() and not value.lstrip().startswith(DIFF_PREFIX):
                issues.append(
                    CaseIssue(
                        code="bad_patch",
                        field=field,
                        message=f"{field} 不是 unified diff（应以 {DIFF_PREFIX} 开头）",
                    )
                )
        for field in TEST_SET_FIELDS:
            problem = _validate_test_set(raw.get(field))
            if problem:
                issues.append(
                    CaseIssue(
                        code="invalid_field_format",
                        field=field,
                        message=f"{field} {problem}",
                    )
                )
        return issues

    # -- standard mapping ---------------------------------------------------

    def normalize_case(self, raw: Mapping[str, Any]) -> NormalizedCase:
        extra = {k: v for k, v in raw.items() if k not in CORE_FIELDS}
        return NormalizedCase(
            case_key=str(raw["instance_id"]),
            repo=raw.get("repo"),
            base_commit=raw.get("base_commit"),
            problem_statement=raw["problem_statement"],
            reference_patch=raw.get("patch"),
            test_patch=raw.get("test_patch"),
            expected_metrics={
                "fail_to_pass": _as_test_set(raw.get("FAIL_TO_PASS")),
                "pass_to_pass": _as_test_set(raw.get("PASS_TO_PASS")),
            },
            extra=extra,
        )

    # -- metrics ------------------------------------------------------------

    def metric_keys(self) -> list[str]:
        return list(METRIC_CONFIGS)

    def metric_configs(self) -> dict[str, dict[str, Any]]:
        return METRIC_CONFIGS

    # -- visibility ----------------------------------------------------------

    def case_visibility(self) -> dict[str, str]:
        """任务包两类权限的标注（07 §1.1：参考答案/测试补丁 Agent 不可见）。"""

        return {
            "problem_statement": "agent",
            "reference_patch": "scoring_only",
            "test_patch": "scoring_only",
        }

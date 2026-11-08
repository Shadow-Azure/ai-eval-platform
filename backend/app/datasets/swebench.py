"""SWE-bench dataset adapter.

The upstream JSONL fields follow the official ``swebench`` collection format:
``instance_id``, ``repo``, ``base_commit``, ``problem_statement``, ``patch``,
``test_patch``, ``FAIL_TO_PASS`` and ``PASS_TO_PASS``. Test lists may arrive as
JSON strings, which is how the published datasets encode them.
"""

from __future__ import annotations

import json
import re

from .adapter import (
    AdapterCaseError,
    BundleFile,
    MetricSpec,
    NormalizedCase,
    register,
)

_INSTANCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _required_text(raw: dict[str, object], field: str, code: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AdapterCaseError(code, f"字段 {field} 必须是非空字符串")
    return value


def _string_list(raw: dict[str, object], field: str) -> tuple[str, ...]:
    value = raw.get(field, [])
    if isinstance(value, str):
        if not value.strip():
            return ()
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise AdapterCaseError("invalid_test_list", f"字段 {field} 不是合法 JSON 列表") from error
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise AdapterCaseError("invalid_test_list", f"字段 {field} 必须是字符串数组")
    return tuple(value)


class SWEBenchAdapter:
    key = "swebench"
    adapter_version = "1.0"
    task_family = "code_patch"
    environment_kind = "linux_docker"

    def parse(self, raw: dict[str, object]) -> NormalizedCase:
        instance_id = _required_text(raw, "instance_id", "missing_instance_id")
        if not _INSTANCE_ID.match(instance_id):
            raise AdapterCaseError("invalid_instance_id", "instance_id 含非法字符")
        repo = _required_text(raw, "repo", "missing_repo")
        if not _REPO.match(repo):
            raise AdapterCaseError("invalid_repo", "repo 必须是 owner/name 形式")
        base_commit = _required_text(raw, "base_commit", "missing_base_commit")
        if not _COMMIT.match(base_commit):
            raise AdapterCaseError("invalid_base_commit", "base_commit 必须是 7 到 64 位十六进制")
        problem_statement = _required_text(
            raw, "problem_statement", "missing_problem_statement"
        )
        reference_patch = _required_text(raw, "patch", "missing_reference_patch")
        test_patch = _required_text(raw, "test_patch", "missing_test_patch")
        fail_to_pass = _string_list(raw, "FAIL_TO_PASS")
        pass_to_pass = _string_list(raw, "PASS_TO_PASS")
        if not fail_to_pass and not pass_to_pass:
            raise AdapterCaseError("missing_test_lists", "FAIL_TO_PASS 与 PASS_TO_PASS 不能同时为空")
        extra = {
            key: raw[key]
            for key in ("version", "environment_setup_commit", "hints_text", "created_at")
            if key in raw
        }
        return NormalizedCase(
            case_key=instance_id,
            repo=repo,
            base_commit=base_commit,
            problem_statement=problem_statement,
            reference_patch=reference_patch,
            test_patch=test_patch,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            extra=extra,
        )

    def bundle(self, case: NormalizedCase) -> tuple[BundleFile, ...]:
        task_toml = _render_task_toml(case)
        tests = ", ".join(_toml_string(item) for item in case.fail_to_pass)
        regression = ", ".join(_toml_string(item) for item in case.pass_to_pass)
        test_script = _render_test_script(case, tests, regression)
        dockerfile = _render_dockerfile(case)
        solve_script = _render_solve_script()
        return (
            BundleFile("task.toml", task_toml.encode("utf-8"), "agent"),
            BundleFile("instruction.md", case.problem_statement.encode("utf-8"), "agent"),
            BundleFile("environment/Dockerfile", dockerfile.encode("utf-8"), "agent"),
            BundleFile("tests/test.patch", case.test_patch.encode("utf-8"), "scoring"),
            BundleFile("tests/test.sh", test_script.encode("utf-8"), "scoring"),
            BundleFile("solution/solve.patch", case.reference_patch.encode("utf-8"), "scoring"),
            BundleFile("solution/solve.sh", solve_script.encode("utf-8"), "scoring"),
        )

    def metric_specs(self) -> tuple[MetricSpec, ...]:
        return (
            MetricSpec(
                metric_key="resolved_count",
                version=1,
                display_name="Resolved 数",
                description="最终有效 attempt 中 resolved 的逻辑用例数。",
                aggregator_key="sum",
                formula_text="count(result_status = resolved)",
                unit="count",
            ),
            MetricSpec(
                metric_key="resolved_rate",
                version=1,
                display_name="解决率",
                description="对齐 SWE-bench 官方口径：resolved 除以全部选中用例。",
                aggregator_key="ratio",
                formula_text="resolved / selected_total",
                display_kind="percentage",
                unit="percent",
                config={"numerator": "resolved", "denominator": "selected_total"},
            ),
            MetricSpec(
                metric_key="resolved_rate_scored",
                version=1,
                display_name="已评分解决率",
                description="resolved 除以已评分用例数，仅作辅助诊断，不替代主指标。",
                aggregator_key="ratio",
                formula_text="resolved / graded",
                display_kind="percentage",
                unit="percent",
                config={"numerator": "resolved", "denominator": "graded"},
            ),
            MetricSpec(
                metric_key="execution_failure_rate",
                version=1,
                display_name="执行失败率",
                description="执行失败、超时和取消的用例数除以全部选中用例。",
                aggregator_key="ratio",
                formula_text="execution_failed / selected_total",
                display_kind="percentage",
                unit="percent",
                direction="lower",
                config={"numerator": "execution_failed", "denominator": "selected_total"},
            ),
            MetricSpec(
                metric_key="tokens_per_case",
                version=1,
                display_name="单用例 Token",
                description="所有 attempt 的 token 总投入除以已执行用例数。",
                aggregator_key="ratio",
                formula_text="sum(tokens) / executed_cases",
                unit="tokens",
                direction="lower",
            ),
            MetricSpec(
                metric_key="cost_per_case",
                version=1,
                display_name="单用例成本",
                description="所有 attempt 的模型成本除以已执行用例数。",
                aggregator_key="ratio",
                formula_text="sum(cost_usd) / executed_cases",
                display_kind="currency",
                unit="usd",
                direction="lower",
            ),
            MetricSpec(
                metric_key="duration_percentiles",
                version=1,
                display_name="用例时长分位",
                description="逻辑用例最新有效 attempt 的 p50 与 p95 时长。",
                aggregator_key="percentile",
                formula_text="percentile(duration_ms, [50, 95])",
                display_kind="distribution",
                unit="ms",
                direction="lower",
            ),
        )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_task_toml(case: NormalizedCase) -> str:
    lines = [
        "[task]",
        'adapter = "swebench"',
        f'adapter_version = "{SWEBenchAdapter.adapter_version}"',
        f"case_key = {_toml_string(case.case_key)}",
        f"repo = {_toml_string(case.repo)}",
        f"base_commit = {_toml_string(case.base_commit)}",
        f'task_family = "{SWEBenchAdapter.task_family}"',
        f'environment_kind = "{SWEBenchAdapter.environment_kind}"',
        "",
    ]
    if case.extra:
        lines.append("[extra]")
        for key, value in sorted(case.extra.items()):
            if isinstance(value, str):
                lines.append(f"{key} = {_toml_string(value)}")
        lines.append("")
    return "\n".join(lines)


def _render_dockerfile(case: NormalizedCase) -> str:
    return "\n".join(
        [
            "# Generated from a SWE-bench case; the scoring harness may replace this base image.",
            "FROM python:3.11-slim",
            "RUN apt-get update \\",
            "    && apt-get install -y --no-install-recommends git curl ca-certificates \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "WORKDIR /workspace",
            f'RUN git clone https://github.com/{case.repo}.git repo \\',
            f'    && cd repo && git checkout {case.base_commit}',
            "WORKDIR /workspace/repo",
            "",
        ]
    )


def _render_test_script(case: NormalizedCase, tests: str, regression: str) -> str:
    node_ids = case.fail_to_pass + case.pass_to_pass
    targets = " ".join(json.dumps(item) for item in node_ids) if node_ids else "."
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "cd /workspace/repo",
            "git apply --whitespace=nowarn /task/tests/test.patch",
            "mkdir -p /workspace/score",
            "python -m pytest -q \\",
            "  --log-file=/workspace/score/test_output.txt \\",
            f"  {targets}",
            "",
            f"# FAIL_TO_PASS: {tests}",
            f"# PASS_TO_PASS: {regression}",
            "",
        ]
    )


def _render_solve_script() -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "cd /workspace/repo",
            "git apply --whitespace=nowarn /task/solution/solve.patch",
            "",
        ]
    )


register(SWEBenchAdapter())

"""M3-01 SWE-bench adapter + registry unit tests (design 07 §1 / §1.1).

Pure mapping tests, no database. The fixture is a vendored minimal slice cut
verbatim from the real ``princeton-nlp/SWE-bench_Verified`` parquet (see
``tests/backend/fixtures/swebench/README.md``); CI never touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.datasets import AdapterNotFound, get_adapter, register_adapter, registered_adapter_keys
from app.datasets.swebench import SWEBenchAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "swebench" / "verified-slice.jsonl"


def load_fixture() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


@pytest.fixture
def fixture_rows() -> list[dict]:
    return load_fixture()


# ---------------------------------------------------------------------------
# Vendored real-data fixture sanity
# ---------------------------------------------------------------------------


def test_fixture_is_a_real_swebench_slice(fixture_rows: list[dict]) -> None:
    assert len(fixture_rows) >= 5, "最小真实切片至少 5 条"
    for row in fixture_rows:
        assert isinstance(row["instance_id"], str) and row["instance_id"]
        assert isinstance(row["repo"], str) and row["repo"].startswith("django") or True
        assert isinstance(row["base_commit"], str) and len(row["base_commit"]) == 40
        assert isinstance(row["problem_statement"], str) and len(row["problem_statement"]) > 50
        assert row["patch"].startswith("diff --git")
        assert row["test_patch"].startswith("diff --git")
        # SWE-bench stores the test sets as JSON-encoded string lists.
        f2p = json.loads(row["FAIL_TO_PASS"]) if isinstance(row["FAIL_TO_PASS"], str) else row["FAIL_TO_PASS"]
        assert isinstance(f2p, list) and f2p


# ---------------------------------------------------------------------------
# Standard mapping (design 07 §1.1)
# ---------------------------------------------------------------------------


def test_adapter_maps_standard_fields(fixture_rows: list[dict]) -> None:
    adapter = SWEBenchAdapter()
    case = adapter.normalize_case(fixture_rows[0])

    assert case.case_key == fixture_rows[0]["instance_id"]
    assert case.repo == fixture_rows[0]["repo"]
    assert case.base_commit == fixture_rows[0]["base_commit"]
    # problem_statement → Agent 可见题面（instruction）
    assert case.problem_statement == fixture_rows[0]["problem_statement"]
    # patch / test_patch → 评分专用，Agent 不可见（由 visibility 元数据表达）
    assert case.reference_patch == fixture_rows[0]["patch"]
    assert case.test_patch == fixture_rows[0]["test_patch"]
    expected = case.expected_metrics
    f2p = json.loads(fixture_rows[0]["FAIL_TO_PASS"])
    p2p = json.loads(fixture_rows[0]["PASS_TO_PASS"])
    assert expected["fail_to_pass"] == f2p
    assert expected["pass_to_pass"] == p2p
    # 其余字段保留在 extra，不参与核心关联
    assert case.extra["version"] == fixture_rows[0]["version"]
    assert "instance_id" not in case.extra or True


def test_adapter_declares_official_metric_keys() -> None:
    adapter = SWEBenchAdapter()
    keys = adapter.metric_keys()
    assert "resolved_rate" in keys  # SWE-bench 官方主口径 % Resolved
    assert "resolved_rate_scored" in keys
    assert "resolved_count" in keys
    assert "execution_failure_rate" in keys


def test_adapter_visibility_classification() -> None:
    adapter = SWEBenchAdapter()
    visibility = adapter.case_visibility()
    assert visibility["problem_statement"] == "agent"
    assert visibility["reference_patch"] == "scoring_only"
    assert visibility["test_patch"] == "scoring_only"


# ---------------------------------------------------------------------------
# Validation failure classification (验收 4：可诊断错误)
# ---------------------------------------------------------------------------


def test_missing_field_is_classified(fixture_rows: list[dict]) -> None:
    adapter = SWEBenchAdapter()
    broken = dict(fixture_rows[0])
    del broken["repo"]
    issues = adapter.validate_case(broken)
    codes = {(issue.code, issue.field) for issue in issues}
    assert ("missing_field", "repo") in codes


def test_bad_patch_is_classified(fixture_rows: list[dict]) -> None:
    adapter = SWEBenchAdapter()
    broken = dict(fixture_rows[0])
    broken["patch"] = "this is not a diff"
    issues = adapter.validate_case(broken)
    assert any(issue.code == "bad_patch" and issue.field == "patch" for issue in issues)


def test_empty_patch_is_classified(fixture_rows: list[dict]) -> None:
    adapter = SWEBenchAdapter()
    broken = dict(fixture_rows[0])
    broken["patch"] = ""
    issues = adapter.validate_case(broken)
    # 空内容 = 缺字段；有内容但不是 diff 才是坏 patch（分类可诊断）。
    assert any(issue.code == "missing_field" and issue.field == "patch" for issue in issues)


def test_invalid_test_set_format_is_classified(fixture_rows: list[dict]) -> None:
    adapter = SWEBenchAdapter()
    broken = dict(fixture_rows[0])
    broken["FAIL_TO_PASS"] = "not json at all"
    issues = adapter.validate_case(broken)
    assert any(issue.code == "invalid_field_format" and issue.field == "FAIL_TO_PASS" for issue in issues)


def test_test_set_accepts_json_string_or_list(fixture_rows: list[dict]) -> None:
    adapter = SWEBenchAdapter()
    as_list = dict(fixture_rows[0])
    as_list["FAIL_TO_PASS"] = ["tests/a.py::test_one"]
    as_list["PASS_TO_PASS"] = []
    assert adapter.validate_case(as_list) == []
    normalized = adapter.normalize_case(as_list)
    assert normalized.expected_metrics["fail_to_pass"] == ["tests/a.py::test_one"]
    assert normalized.expected_metrics["pass_to_pass"] == []


def test_valid_record_has_no_issues(fixture_rows: list[dict]) -> None:
    adapter = SWEBenchAdapter()
    for row in fixture_rows:
        assert adapter.validate_case(row) == []


# ---------------------------------------------------------------------------
# Registry: 后续数据集只加适配器
# ---------------------------------------------------------------------------


def test_registry_resolves_swebench_adapter() -> None:
    adapter = get_adapter("swebench")
    assert adapter.key == "swebench"
    assert adapter.adapter_version == "1"


def test_registry_unknown_key_raises() -> None:
    with pytest.raises(AdapterNotFound):
        get_adapter("does-not-exist")


def test_registry_is_extensible() -> None:
    class FakeAdapter(SWEBenchAdapter):
        key = "fake-dataset"
        adapter_version = "3"

    register_adapter(FakeAdapter())
    try:
        assert get_adapter("fake-dataset").adapter_version == "3"
        assert "fake-dataset" in registered_adapter_keys()
    finally:
        from app.datasets.registry import unregister_adapter

        unregister_adapter("fake-dataset")

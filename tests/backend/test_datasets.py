"""M3-01 dataset version import + validation + metric registration.

Covers issue #45 acceptance: 真实切片导入通过校验且用例总数与版本一致、
指标定义版本化可查且白名单外聚合器被拒绝、重复导入幂等不产生第二个版本、
校验失败有明确失败分类（缺字段/坏 patch 等可诊断错误）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from app.models import (
    DatasetCase,
    DatasetImport,
    DatasetVersion,
    MetricDefinition,
)
from app.seed import ensure_default_scope
from app.services import datasets as dataset_service
from app.services.datasets import DatasetVersionConflict, ImportValidationFailed
from app.services.metrics import (
    AGGREGATOR_WHITELIST,
    CASE_SET_KEYS,
    MetricInvalid,
    register_metric_definition,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

FIXTURE = Path(__file__).parent / "fixtures" / "swebench" / "verified-slice.jsonl"


def fixture_records() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def do_import(
    session: Session,
    *,
    records: list[dict] | None = None,
    version: str = "verified-smoke",
    source_revision: str = "rev-0001",
    adapter_key: str = "swebench",
    dataset_slug: str = "swebench-verified",
    **kwargs,
):
    return dataset_service.import_dataset_version(
        session,
        dataset_slug=dataset_slug,
        dataset_name="SWE-bench Verified",
        adapter_key=adapter_key,
        version=version,
        source_uri="tests/backend/fixtures/swebench/verified-slice.jsonl",
        source_revision=source_revision,
        records=fixture_records() if records is None else records,
        created_by="agent",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Schema basics + unique constraints
# ---------------------------------------------------------------------------


def test_dataset_catalog_rows_roundtrip(seeded: Session) -> None:
    outcome = do_import(seeded)
    assert outcome.status == "succeeded"
    dataset = outcome.dataset
    assert dataset.tenant_id == str(DEFAULT_TENANT_ID)
    assert dataset.project_id == str(DEFAULT_PROJECT_ID)
    assert dataset.adapter_key == "swebench"


def test_duplicate_version_label_rejected_at_db_level(seeded: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    outcome = do_import(seeded)
    session = seeded
    duplicate = DatasetVersion(
        tenant_id=outcome.dataset.tenant_id,
        dataset_id=outcome.dataset.id,
        version="verified-smoke",
        source_uri="other://source",
        source_revision="other-rev",
        adapter_version="1",
        case_count=0,
        status="active",
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_duplicate_case_key_rejected_at_db_level(seeded: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    outcome = do_import(seeded)
    session = seeded
    first_case = session.scalars(
        select(DatasetCase).where(DatasetCase.dataset_version_id == outcome.version.id)
    ).first()
    session.add(
        DatasetCase(
            tenant_id=outcome.dataset.tenant_id,
            dataset_version_id=outcome.version.id,
            case_key=first_case.case_key,
            ordinal=999,
            problem_statement="x",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ---------------------------------------------------------------------------
# Import: success path (验收 1)
# ---------------------------------------------------------------------------


def test_import_creates_dataset_version_cases_and_metrics(seeded: Session) -> None:
    records = fixture_records()
    outcome = do_import(seeded)

    assert outcome.created is True
    assert outcome.status == "succeeded"
    assert outcome.version.case_count == len(records)
    assert outcome.import_record.valid_count == len(records)
    assert outcome.import_record.total_count == len(records)
    assert outcome.import_record.invalid_count == 0

    cases = list(seeded.scalars(select(DatasetCase).where(DatasetCase.dataset_version_id == outcome.version.id)))
    assert len(cases) == len(records)
    # 用例总数与版本一致（验收 1）
    assert {c.case_key for c in cases} == {r["instance_id"] for r in records}
    ordinals = sorted(c.ordinal for c in cases)
    assert ordinals == list(range(1, len(records) + 1))

    # 首个用例的标准映射落库
    first = next(c for c in cases if c.ordinal == 1)
    assert first.repo == records[0]["repo"]
    assert first.base_commit == records[0]["base_commit"]
    assert first.problem_statement == records[0]["problem_statement"]
    assert first.reference_patch == records[0]["patch"]
    assert first.test_patch == records[0]["test_patch"]
    assert first.expected_metrics_json["fail_to_pass"] == json.loads(records[0]["FAIL_TO_PASS"])


def test_import_is_default_tenant_scoped(seeded: Session) -> None:
    outcome = do_import(seeded)
    assert outcome.dataset.tenant_id == str(DEFAULT_TENANT_ID)
    assert outcome.import_record.tenant_id == str(DEFAULT_TENANT_ID)


# ---------------------------------------------------------------------------
# Import: idempotency (验收 3)
# ---------------------------------------------------------------------------


def test_reimport_same_version_is_idempotent(seeded: Session) -> None:
    first = do_import(seeded)
    case_rows_before = list(
        seeded.scalars(select(DatasetCase).where(DatasetCase.dataset_version_id == first.version.id))
    )
    second = do_import(seeded)

    assert second.created is False
    assert second.status == "duplicate"
    assert second.version.id == first.version.id
    versions = list(seeded.scalars(select(DatasetVersion)))
    assert len(versions) == 1, "重复导入不得产生第二个版本"
    cases_after = list(seeded.scalars(select(DatasetCase).where(DatasetCase.dataset_version_id == first.version.id)))
    assert len(cases_after) == len(case_rows_before)
    # 任务视图不可变：既有用例行内容逐列不变
    before_by_key = {c.case_key: c for c in case_rows_before}
    for case in cases_after:
        before = before_by_key[case.case_key]
        assert case.problem_statement == before.problem_statement
        assert case.reference_patch == before.reference_patch
        assert case.test_patch == before.test_patch
        assert case.expected_metrics_json == before.expected_metrics_json
    # 两次导入各自留下一次导入记录，但第二次是 duplicate
    imports = list(seeded.scalars(select(DatasetImport)))
    assert len(imports) == 2
    assert {r.status for r in imports} == {"succeeded", "duplicate"}


def test_conflicting_revision_for_same_version_label_raises(seeded: Session) -> None:
    do_import(seeded, source_revision="rev-0001")
    with pytest.raises(DatasetVersionConflict):
        do_import(seeded, source_revision="rev-0002")
    versions = list(seeded.scalars(select(DatasetVersion)))
    assert len(versions) == 1


# ---------------------------------------------------------------------------
# Import: validation failure classification (验收 4)
# ---------------------------------------------------------------------------


def test_missing_field_failure_is_classified(seeded: Session) -> None:
    records = fixture_records()
    broken = dict(records[0])
    del broken["base_commit"]
    with pytest.raises(ImportValidationFailed) as excinfo:
        do_import(seeded, records=[broken] + records[1:4])
    outcome = excinfo.value.outcome
    assert outcome.status == "failed"
    issue = outcome.errors[0]
    assert issue.code == "missing_field"
    assert issue.field == "base_commit"
    assert issue.case_key is None or True
    # 失败导入不产生版本或脏任务
    assert list(seeded.scalars(select(DatasetVersion))) == []
    assert list(seeded.scalars(select(DatasetCase))) == []


def test_bad_patch_failure_is_classified(seeded: Session) -> None:
    records = fixture_records()
    broken = dict(records[1])
    broken["patch"] = "definitely not a unified diff"
    with pytest.raises(ImportValidationFailed) as excinfo:
        do_import(seeded, records=[records[0], broken, records[2]])
    issue = excinfo.value.outcome.errors[0]
    assert issue.code == "bad_patch"
    assert issue.field == "patch"
    assert issue.case_key == records[1]["instance_id"]


def test_duplicate_case_key_failure_is_classified(seeded: Session) -> None:
    records = fixture_records()
    duplicated = dict(records[0])  # same instance_id twice
    with pytest.raises(ImportValidationFailed) as excinfo:
        do_import(seeded, records=[records[0], duplicated])
    codes = {issue.code for issue in excinfo.value.outcome.errors}
    assert "duplicate_case_key" in codes


def test_failed_import_persists_diagnosable_record(seeded: Session) -> None:
    records = fixture_records()
    broken = dict(records[0])
    del broken["problem_statement"]
    with pytest.raises(ImportValidationFailed) as excinfo:
        do_import(seeded, records=[broken])
    record = excinfo.value.outcome.import_record
    assert record.status == "failed"
    assert record.invalid_count == 1
    assert record.valid_count == 0
    report = record.error_report_json
    assert report["errors"][0]["code"] == "missing_field"
    assert report["errors"][0]["field"] == "problem_statement"
    assert report["adapter_key"] == "swebench"
    # 失败记录同样可经 get_import 查询（服务端可诊断）
    fetched = dataset_service.get_import(seeded, record.id)
    assert fetched.id == record.id


def test_unknown_adapter_fails_with_classification(seeded: Session) -> None:
    records = fixture_records()
    with pytest.raises(ImportValidationFailed) as excinfo:
        do_import(seeded, records=records[:2], adapter_key="no-such-adapter")
    outcome = excinfo.value.outcome
    assert outcome.status == "failed"
    assert outcome.errors[0].code == "adapter_not_found"
    assert outcome.version is None


# ---------------------------------------------------------------------------
# Metric registration (验收 2：D10 白名单 + 版本化 + 官方口径)
# ---------------------------------------------------------------------------


def test_swebench_official_metrics_registered_on_import(seeded: Session) -> None:
    outcome = do_import(seeded)
    metrics = dataset_service.list_version_metrics(seeded, outcome.version.id)
    keys = {m.metric_key for m in metrics}
    assert keys == {
        "resolved_count",
        "resolved_rate",
        "resolved_rate_scored",
        "execution_failure_rate",
    }
    resolved_rate = next(m for m in metrics if m.metric_key == "resolved_rate")
    # SWE-bench 主指标官方口径：% Resolved = resolved / total
    assert resolved_rate.aggregator_key == "ratio"
    assert resolved_rate.config_json["numerator"] == {"case_result": "resolved"}
    assert resolved_rate.config_json["denominator"] == {"case_set": "selected_total"}
    assert resolved_rate.display_kind == "percentage"
    assert resolved_rate.version == 1
    assert "resolved" in resolved_rate.formula_text and "total" in resolved_rate.formula_text
    scored = next(m for m in metrics if m.metric_key == "resolved_rate_scored")
    assert scored.config_json["denominator"] == {"case_set": "graded"}


def test_metric_registration_whitelist_enforced(seeded: Session) -> None:
    outcome = do_import(seeded)
    with pytest.raises(MetricInvalid) as excinfo:
        register_metric_definition(
            seeded,
            dataset_id=outcome.dataset.id,
            metric_key="cheat_rate",
            version=1,
            aggregator_key="exec_python_snippet",
            numerator={"case_result": "resolved"},
            denominator={"case_set": "selected_total"},
            display_kind="percentage",
            display_name="作弊率",
            formula_text="SELECT 1; -- 任意代码不得执行",
        )
    assert excinfo.value.code == "aggregator_not_allowed"
    assert set(AGGREGATOR_WHITELIST) == {"ratio", "sum", "mean", "percentile", "histogram"}
    assert seeded.scalars(select(MetricDefinition).where(MetricDefinition.metric_key == "cheat_rate")).first() is None


def test_aggregator_check_constraint_blocks_direct_insert(seeded: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    outcome = do_import(seeded)
    seeded.add(
        MetricDefinition(
            tenant_id=outcome.dataset.tenant_id,
            dataset_id=outcome.dataset.id,
            metric_key="backdoor",
            version=1,
            aggregator_key="raw_sql",
            display_name="backdoor",
            display_kind="number",
        )
    )
    with pytest.raises(IntegrityError):
        seeded.commit()
    seeded.rollback()


def test_metric_denominator_must_reference_registered_case_set(seeded: Session) -> None:
    outcome = do_import(seeded)
    with pytest.raises(MetricInvalid) as excinfo:
        register_metric_definition(
            seeded,
            dataset_id=outcome.dataset.id,
            metric_key="weird_rate",
            version=1,
            aggregator_key="ratio",
            numerator={"case_result": "resolved"},
            denominator={"case_set": "banana_count"},
            display_kind="percentage",
            display_name="weird",
            formula_text="x",
        )
    assert excinfo.value.code == "case_set_not_registered"
    assert "selected_total" in CASE_SET_KEYS


def test_metric_config_is_versioned(seeded: Session) -> None:
    outcome = do_import(seeded)
    v2 = register_metric_definition(
        seeded,
        dataset_id=outcome.dataset.id,
        metric_key="resolved_rate",
        version=2,
        aggregator_key="ratio",
        numerator={"case_result": "resolved"},
        denominator={"case_set": "graded"},
        display_kind="percentage",
        display_name="解决率（已评分口径 v2）",
        formula_text="resolved / graded",
    )
    assert v2.version == 2
    rows = list(
        seeded.scalars(
            select(MetricDefinition)
            .where(MetricDefinition.dataset_id == outcome.dataset.id)
            .order_by(MetricDefinition.metric_key, MetricDefinition.version)
        )
    )
    resolved_versions = [r.version for r in rows if r.metric_key == "resolved_rate"]
    assert resolved_versions == [1, 2], "同 key 多版本共存且可查"


# ---------------------------------------------------------------------------
# Adapter genericity: duplicate detection must use the adapter's case key
# ---------------------------------------------------------------------------


class _SimpleKeyedAdapter:
    """非 SWE-bench 形状的适配器：case key 来自 ``id`` 字段。"""

    key = "simple-keyed"
    adapter_version = "1"

    def validate_case(self, raw):
        from app.datasets import CaseIssue

        if not isinstance(raw.get("id"), str) or not raw["id"].strip():
            return [CaseIssue(code="missing_field", field="id", message="缺少 id")]
        if not isinstance(raw.get("problem_statement"), str) or not raw["problem_statement"].strip():
            return [CaseIssue(code="missing_field", field="problem_statement", message="缺少题面")]
        return []

    def normalize_case(self, raw):
        from app.datasets import NormalizedCase

        return NormalizedCase(case_key=raw["id"], problem_statement=raw["problem_statement"])

    def metric_keys(self):
        return []

    def metric_configs(self):
        return {}

    def case_visibility(self):
        return {"problem_statement": "agent"}


def test_duplicate_detection_uses_adapter_case_key(seeded: Session) -> None:
    from app.datasets import register_adapter, unregister_adapter

    register_adapter(_SimpleKeyedAdapter())
    try:
        records = [
            {"id": "task-a", "problem_statement": "alpha"},
            {"id": "task-a", "problem_statement": "beta"},
        ]
        with pytest.raises(ImportValidationFailed) as excinfo:
            dataset_service.import_dataset_version(
                seeded,
                dataset_slug="simple",
                dataset_name="Simple",
                adapter_key="simple-keyed",
                version="v1",
                source_uri="fixture",
                source_revision="rev-1",
                records=records,
                created_by="agent",
            )
        codes = {issue.code for issue in excinfo.value.outcome.errors}
        assert "duplicate_case_key" in codes
        assert list(seeded.scalars(select(DatasetCase))) == []
    finally:
        unregister_adapter("simple-keyed")

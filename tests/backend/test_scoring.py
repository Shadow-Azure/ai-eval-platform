"""M3-04 official score parser, per-test results and metric snapshots."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.datasets.swebench import SWEBenchAdapter
from app.models import (
    Dataset,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    MetricDefinition,
    ScoreArtifact,
    TestCaseResult,
)
from app.scoring.service import ingest_score_report, recompute_metrics
from app.scoring.swebench import ScoreParseError, parse_report
from app.seed import ensure_default_scope
from sqlalchemy import func, select
from sqlalchemy.orm import Session

CASE_KEY = "django__django-11019"
REPORT = {
    CASE_KEY: {
        "resolved": True,
        "patch_successfully_applied": True,
        "tests_status": {
            "FAIL_TO_PASS": {
                "success": [
                    "tests/test_forms.py::TestForms::test_field_order",
                    "tests/test_forms.py::TestForms::test_label",
                ],
                "failure": ["tests/test_forms.py::TestForms::test_widget"],
            },
            "PASS_TO_PASS": {
                "success": ["tests/test_forms.py::TestForms::test_basic"],
                "failure": [],
            },
        },
    }
}


def _dataset(session: Session) -> Dataset:
    tenant, _, project = ensure_default_scope(session)
    dataset = Dataset(
        tenant_id=tenant.id,
        project_id=project.id,
        slug="swebench-scoring",
        name="SWE-bench scoring",
        adapter_key="swebench",
        task_family="code_patch",
        environment_kind="linux_docker",
        metadata_json={},
    )
    session.add(dataset)
    session.flush()
    for metric_key, spec in SWEBenchAdapter().metric_configs().items():
        session.add(
            MetricDefinition(
                tenant_id=tenant.id,
                dataset_id=dataset.id,
                metric_key=metric_key,
                version=spec["version"],
                display_name=spec["display_name"],
                description=spec.get("description"),
                aggregator_key=spec["aggregator_key"],
                formula_text=spec.get("formula_text"),
                display_kind=spec["display_kind"],
                direction=spec.get("direction", "higher_is_better"),
                denominator_policy=spec.get("denominator_policy"),
                config_json=spec,
            )
        )
    session.flush()
    return dataset


def _run_with_cases(session: Session, dataset: Dataset) -> tuple[EvalRun, list[EvalCaseRun]]:
    tenant, _, project = ensure_default_scope(session)
    run = EvalRun(
        tenant_id=tenant.id,
        project_id=project.id,
        name="scoring run",
        status="completed",
        idempotency_key="scoring-run",
        created_by="tester",
        total_cases=3,
        dataset_version_id=None,
    )
    session.add(run)
    session.flush()
    specs = [
        ("case-resolved", "django__django-11019", "succeeded", "resolved", 1000),
        ("case-unresolved", "sympy__sympy-20590", "succeeded", "unresolved", 3000),
        ("case-failed", "pytest-dev__pytest-7432", "failed", "not_scored", None),
    ]
    cases: list[EvalCaseRun] = []
    for dataset_case_id, case_key, execution_status, result_status, duration in specs:
        case = EvalCaseRun(
            tenant_id=tenant.id,
            eval_run_id=run.id,
            dataset_case_id=dataset_case_id,
            case_key=case_key,
            execution_status=execution_status,
            result_status=result_status,
            latest_attempt_no=1,
            duration_ms=duration,
        )
        session.add(case)
        session.flush()
        session.add(
            EvalCaseAttempt(
                tenant_id=tenant.id,
                eval_case_run_id=case.id,
                attempt_no=1,
                status="succeeded" if execution_status == "succeeded" else "failed",
            )
        )
        cases.append(case)
    session.commit()
    return run, cases


def test_parse_report_keeps_test_level_groups() -> None:
    parsed = parse_report(REPORT, CASE_KEY)
    assert parsed.result_status == "resolved"
    assert len(parsed.tests) == 4
    groups = {(test.test_group, test.native_status, test.outcome) for test in parsed.tests}
    assert ("F2P", "success", "passed") in groups
    assert ("F2P", "failure", "failed") in groups
    assert ("P2P", "success", "passed") in groups
    assert parsed.tests[0].suite_name == "tests/test_forms.py"


def test_parse_report_marks_unresolved_and_missing_cases() -> None:
    report = {"case-a": {"resolved": False, "tests_status": {}}}
    parsed = parse_report(report, "case-a")
    assert parsed.result_status == "unresolved"
    assert parsed.tests == ()
    with pytest.raises(ScoreParseError):
        parse_report(report, "case-b")


def test_ingest_score_report_is_idempotent(session: Session) -> None:
    dataset = _dataset(session)
    _, cases = _run_with_cases(session, dataset)
    case = cases[0]
    attempt = session.scalars(
        select(EvalCaseAttempt).where(EvalCaseAttempt.eval_case_run_id == case.id)
    ).one()
    object_key = "evaluations/run/attempt/score/report.json"

    artifact, parsed = ingest_score_report(
        session, case_run=case, attempt=attempt, report=REPORT, object_key=object_key
    )
    session.commit()
    assert parsed is not None and parsed.result_status == "resolved"
    assert artifact.parsed_at is not None
    assert artifact.object_key == object_key
    assert case.result_status == "resolved"
    assert (
        session.scalar(
            select(func.count())
            .select_from(TestCaseResult)
            .where(TestCaseResult.eval_case_attempt_id == attempt.id)
        )
        == 4
    )

    ingest_score_report(session, case_run=case, attempt=attempt, report=REPORT, object_key=object_key)
    session.commit()
    assert session.scalar(select(func.count()).select_from(TestCaseResult)) == 4
    assert session.scalar(select(func.count()).select_from(ScoreArtifact)) == 1


def test_recompute_metrics_uses_official_denominator(session: Session) -> None:
    dataset = _dataset(session)
    run, _ = _run_with_cases(session, dataset)
    definitions = list(
        session.scalars(
            select(MetricDefinition)
            .where(MetricDefinition.dataset_id == dataset.id)
            .order_by(MetricDefinition.metric_key)
        )
    )
    results = recompute_metrics(session, run=run, definitions=definitions, is_final=True)
    session.commit()
    by_key = {result.metric_key: result for result in results}

    assert by_key["resolved_count"].value_json["value"] == 1
    assert by_key["resolved_rate"].value_json["value"] == pytest.approx(1 / 3)
    assert by_key["resolved_rate"].denominator == Decimal(3)
    assert by_key["resolved_rate_scored"].value_json["value"] == pytest.approx(1 / 2)
    assert by_key["execution_failure_rate"].value_json["value"] == pytest.approx(1 / 3)
    assert all(result.is_final for result in results)


def test_results_and_tests_api(client, session: Session) -> None:
    dataset = _dataset(session)
    run, cases = _run_with_cases(session, dataset)
    definitions = list(session.scalars(select(MetricDefinition)))
    recompute_metrics(session, run=run, definitions=definitions, is_final=True)
    case = cases[0]
    attempt = session.scalars(
        select(EvalCaseAttempt).where(EvalCaseAttempt.eval_case_run_id == case.id)
    ).one()
    ingest_score_report(session, case_run=case, attempt=attempt, report=REPORT)
    session.commit()

    results = client.get(f"/api/v1/eval-runs/{run.id}/results")
    assert results.status_code == 200
    body = results.json()
    assert body["is_final"] is True
    assert {item["metric_key"] for item in body["items"]} >= {
        "resolved_rate",
        "resolved_rate_scored",
    }
    tests = client.get(f"/api/v1/eval-case-runs/{case.id}/tests")
    assert tests.status_code == 200
    payload = tests.json()
    assert payload["attempt"] == 1
    assert len(payload["items"]) == 4
    assert payload["summary"]["f2p_passed"] == 2
    assert payload["summary"]["f2p_failed"] == 1

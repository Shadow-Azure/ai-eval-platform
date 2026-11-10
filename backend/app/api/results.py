from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import EvalCaseAttempt, EvalCaseRun, EvalRun, MetricDefinition, MetricResult, TestCaseResult
from ..scoring.service import case_test_summary
from ..seed import ensure_default_scope
from ..services.evaluations import _iso

router = APIRouter(tags=["results"])


def _scope(session: Session) -> str:
    tenant, _, _ = ensure_default_scope(session)
    return tenant.id


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "request_id": None}},
    )


def _run_or_404(session: Session, tenant_id: str, run_id: str) -> EvalRun:
    run = session.scalar(
        select(EvalRun).where(EvalRun.id == run_id, EvalRun.tenant_id == tenant_id)
    )
    if run is None:
        raise _error(404, "eval_run_not_found", "评测不存在")
    return run


@router.get("/api/v1/eval-runs/{run_id}/results")
def get_eval_results(
    run_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id = _scope(session)
    run = _run_or_404(session, tenant_id, run_id)
    rows = list(
        session.execute(
            select(MetricResult, MetricDefinition)
            .join(
                MetricDefinition,
                MetricDefinition.id == MetricResult.metric_definition_id,
            )
            .where(MetricResult.eval_run_id == run.id)
            .order_by(MetricDefinition.metric_key)
        )
    )
    final_rows = [(result, definition) for result, definition in rows if result.is_final]
    selected = final_rows or rows
    items = []
    for result, definition in selected:
        value = result.value_json.get("value") if isinstance(result.value_json, dict) else None
        items.append(
            {
                "metric_key": result.metric_key,
                "metric_version": result.metric_version,
                "display_name": definition.display_name,
                "description": definition.description,
                "formula_text": definition.formula_text,
                "display_kind": definition.display_kind,
                "unit": definition.unit,
                "direction": definition.direction,
                "denominator_policy": definition.denominator_policy,
                "value": value,
                "numerator": result.numerator,
                "denominator": result.denominator,
                "is_final": result.is_final,
                "generated_at": _iso(result.generated_at),
            }
        )
    return {
        "run_id": run.id,
        "status": run.status,
        "is_final": bool(final_rows),
        "items": items,
    }


@router.get("/api/v1/eval-case-runs/{case_run_id}/tests")
def get_case_tests(
    case_run_id: str,
    attempt: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    tenant_id = _scope(session)
    case_run = session.scalar(
        select(EvalCaseRun).where(
            EvalCaseRun.id == case_run_id, EvalCaseRun.tenant_id == tenant_id
        )
    )
    if case_run is None:
        raise _error(404, "eval_case_run_not_found", "评测用例不存在")
    attempt_statement = select(EvalCaseAttempt).where(
        EvalCaseAttempt.eval_case_run_id == case_run.id
    )
    if attempt is None:
        attempt_statement = attempt_statement.order_by(EvalCaseAttempt.attempt_no.desc())
        case_attempt = session.scalars(attempt_statement.limit(1)).first()
    else:
        case_attempt = session.scalars(
            attempt_statement.where(EvalCaseAttempt.attempt_no == attempt)
        ).first()
    if case_attempt is None:
        return {
            "case_run_id": case_run.id,
            "attempt": None,
            "items": [],
            "summary": {},
        }
    tests = list(
        session.scalars(
            select(TestCaseResult)
            .where(TestCaseResult.eval_case_attempt_id == case_attempt.id)
            .order_by(TestCaseResult.test_group, TestCaseResult.test_name)
        )
    )
    return {
        "case_run_id": case_run.id,
        "attempt": case_attempt.attempt_no,
        "result_status": case_run.result_status,
        "summary": case_test_summary(session, case_attempt.id),
        "items": [
            {
                "id": test.id,
                "suite_name": test.suite_name,
                "test_name": test.test_name,
                "outcome": test.outcome,
                "native_status": test.native_status,
                "test_group": test.test_group,
                "expected": test.expected,
                "duration_ms": test.duration_ms,
                "file_path": test.file_path,
                "line_no": test.line_no,
                "failure_message": test.failure_message,
                "log_object_key": test.log_object_key,
            }
            for test in tests
        ],
    }

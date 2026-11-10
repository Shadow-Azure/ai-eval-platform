"""Evaluation results workbench API (M5-05, design 04 §4/§6).

Read paths power the detail page's cases/config/events tabs; the two retry
paths are the only mutations. All of them are thin adapters over
``services.workbench`` so the CLI/pytest surface and the console share the same
state machine.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_session
from ..services import evaluations as evaluation_service
from ..services import identity, workbench
from .dependencies import require_admin
from .errors import error_detail

router = APIRouter(tags=["eval-workbench"])


def _run_or_404(session: Session, run_id: str):
    try:
        return evaluation_service.get_evaluation(session, run_id)
    except evaluation_service.EvaluationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("eval_run_not_found", "评测不存在"),
        ) from exc


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, workbench.WorkbenchNotFound):
        return HTTPException(
            status_code=404,
            detail=error_detail(exc.code, str(exc)),
        )
    if isinstance(exc, workbench.WorkbenchRejected):
        return HTTPException(
            status_code=exc.status_code,
            detail=error_detail(exc.code, str(exc)),
        )
    return HTTPException(
        status_code=500,
        detail=error_detail("eval_workbench_error", "结果工作台请求失败"),
    )


@router.get("/api/v1/eval-runs/{run_id}/cases")
def list_eval_cases(
    run_id: str,
    status: Annotated[str, Query(max_length=32)] = "all",
    q: Annotated[str | None, Query(max_length=200)] = None,
    execution_status: Annotated[str | None, Query(max_length=32)] = None,
    result_status: Annotated[str | None, Query(max_length=32)] = None,
    trace_status: Annotated[str | None, Query(max_length=32)] = None,
    sort: Annotated[str, Query(max_length=32)] = "ordinal",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
) -> dict:
    run = _run_or_404(session, run_id)
    try:
        return workbench.list_cases(
            session,
            run,
            status=status,
            q=q,
            execution_status=execution_status,
            result_status=result_status,
            trace_status=trace_status,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
    except workbench.WorkbenchRejected as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/eval-runs/{run_id}/config")
def get_eval_config(run_id: str, session: Session = Depends(get_session)) -> dict:
    run = _run_or_404(session, run_id)
    return workbench.config_snapshot(session, run)


@router.get("/api/v1/eval-case-runs/{case_run_id}")
def get_eval_case(case_run_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        return workbench.get_case(session, case_run_id)
    except workbench.WorkbenchNotFound as exc:
        raise _http_error(exc) from exc


@router.post("/api/v1/eval-case-runs/{case_run_id}/retry")
def retry_eval_case(
    case_run_id: str,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        result = workbench.retry_case(
            session,
            case_run_id=case_run_id,
            actor=context.user.email,
        )
    except (workbench.WorkbenchNotFound, workbench.WorkbenchRejected) as exc:
        session.rollback()
        raise _http_error(exc) from exc
    return {"status": "ok", **result}


@router.post("/api/v1/eval-runs/{run_id}/retry-failed")
def retry_eval_failed_cases(
    run_id: str,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        result = workbench.retry_failed_cases(
            session,
            run_id=run_id,
            actor=context.user.email,
        )
    except (workbench.WorkbenchNotFound, workbench.WorkbenchRejected) as exc:
        session.rollback()
        raise _http_error(exc) from exc
    return {"status": "ok", **result}

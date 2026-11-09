"""Synthetic evaluation console API (M2-05, design 04 §2 Eval rows).

Minimal CRUD surface: create (idempotent), list, detail, event cursor and
cancel. All errors use the shared {"error": {code, ...}} envelope.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..services import evaluations as evaluation_service
from ..services import identity
from .dependencies import require_admin
from .errors import error_detail

router = APIRouter(tags=["evaluations"])


class CreateEvaluationRequest(BaseModel):
    name: str = Field(default="合成评测", max_length=200)
    case_keys: list[str] = Field(min_length=1)
    shard_size: int | None = Field(default=None, ge=1, le=256)
    idempotency_key: str | None = Field(default=None, max_length=128)
    # M3-02: optional model binding so the runner can obtain a short-lived
    # gateway token from the shard manifest.
    model_config_id: str | None = Field(default=None, max_length=36)
    model_name: str | None = Field(default=None, max_length=200)
    model_budget_usd: float | None = Field(default=None, ge=0)


@router.post("/api/v1/evaluations")
def create_evaluation(
    payload: CreateEvaluationRequest,
    response: Response,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    settings = get_settings()
    try:
        run, created = evaluation_service.create_synthetic_evaluation(
            session,
            name=payload.name,
            case_keys=payload.case_keys,
            shard_size=payload.shard_size,
            idempotency_key=payload.idempotency_key,
            created_by=context.user.email,
            model_config_id=payload.model_config_id,
            model_name=payload.model_name,
            model_budget_usd=payload.model_budget_usd,
            settings=settings,
        )
    except evaluation_service.EvaluationInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail=error_detail(
                "evaluation_invalid",
                str(exc),
                details={"fields": {exc.field: "invalid"}} if exc.field else None,
            ),
        ) from exc
    # Idempotent replay of an existing run answers 200, fresh creation 201.
    response.status_code = 201 if created else 200
    return _run_body(evaluation_service.evaluation_summary(run), created)


@router.get("/api/v1/evaluations")
def list_evaluations(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    session: Session = Depends(get_session),
) -> dict:
    runs, total = evaluation_service.list_evaluations(session, limit=limit)
    return {
        "status": "ok",
        "total": total,
        "items": [evaluation_service.evaluation_summary(run) for run in runs],
    }


@router.get("/api/v1/evaluations/{run_id}")
def get_evaluation(run_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        run = evaluation_service.get_evaluation(session, run_id)
    except evaluation_service.EvaluationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("evaluation_not_found", "评测不存在"),
        ) from exc
    return evaluation_service.evaluation_detail(session, run)


@router.get("/api/v1/evaluations/{run_id}/events")
def list_evaluation_events(
    run_id: str,
    after_id: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    session: Session = Depends(get_session),
) -> dict:
    try:
        evaluation_service.get_evaluation(session, run_id)
    except evaluation_service.EvaluationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("evaluation_not_found", "评测不存在"),
        ) from exc
    items, next_cursor = evaluation_service.list_events(session, run_id, after_id=after_id, limit=limit)
    return {"status": "ok", "items": items, "next_cursor": next_cursor}


@router.post("/api/v1/evaluations/{run_id}/cancel")
def cancel_evaluation(
    run_id: str,
    session: Session = Depends(get_session),
    _context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        run, replayed = evaluation_service.cancel_evaluation(session, run_id=run_id)
    except evaluation_service.EvaluationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("evaluation_not_found", "评测不存在"),
        ) from exc
    except evaluation_service.EvaluationAlreadyFinal as exc:
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "evaluation_already_final",
                f"评测已终态（{exc.args[0]}），不能取消",
            ),
        ) from exc
    return _run_body(evaluation_service.evaluation_summary(run), not replayed)


def _run_body(summary: dict, created: bool) -> dict:
    return {"created": created, **summary}

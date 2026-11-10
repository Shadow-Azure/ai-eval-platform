"""Synthetic evaluation console API (M2-05, design 04 §2 Eval rows).

Minimal CRUD surface: create (idempotent), list, detail, event cursor and
cancel. All errors use the shared {"error": {code, ...}} envelope.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from ..db import get_session
from ..services import budget as budget_service
from ..services import evaluations as evaluation_service
from ..services import identity
from ..services import preflight as preflight_service
from .dependencies import require_admin
from .errors import error_detail

router = APIRouter(tags=["evaluations"])


class PrepareRequest(BaseModel):
    dataset_version_id: str = Field(min_length=1, max_length=36)
    model_config_id: str = Field(min_length=1, max_length=36)
    model_name: str | None = Field(default=None, max_length=200)
    runtime_key: str = Field(default="claude-code", max_length=64)
    runtime_version: str | None = Field(default=None, max_length=64)
    resource_pool_id: str = Field(min_length=1, max_length=36)
    requested_concurrency: int = Field(default=1, ge=1, le=256)
    scoring_image_digest: str | None = Field(default=None, max_length=128)
    execution_image_digest: str | None = Field(default=None, max_length=128)
    assets_resolved: bool = True


class CreateEvaluationRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    case_keys: list[str] | None = None
    # GitHub allows at most 256 jobs per workflow run; the prepare job plus one
    # matrix job per case must fit, so the shard cap is 255 (design 06 §3.1).
    shard_size: int | None = Field(default=None, ge=1, le=255)
    # M4-01: per-evaluation hard cap for simultaneously running cases.
    requested_concurrency: int | None = Field(default=None, ge=1, le=256)
    idempotency_key: str | None = Field(default=None, max_length=128)
    # M3-02: optional model binding so the runner can obtain a short-lived
    # gateway token from the shard manifest.
    model_config_id: str | None = Field(default=None, max_length=36)
    model_name: str | None = Field(default=None, max_length=200)
    model_budget_usd: float | None = Field(default=None, ge=0)
    # M3-04: real evaluation from an immutable dataset version.
    dataset_version_id: str | None = Field(default=None, max_length=36)
    runtime_key: str | None = Field(default=None, max_length=64)
    runtime_version: str | None = Field(default=None, max_length=64)
    resource_pool_id: str | None = Field(default=None, max_length=36)
    scoring_image_digest: str | None = Field(default=None, max_length=128)
    execution_image_digest: str | None = Field(default=None, max_length=128)
    assets_resolved: bool = True


class ResumeBudgetRequest(BaseModel):
    """M4-05: confirm adding budget to a paused evaluation."""

    additional_budget_usd: Decimal = Field(gt=0, max_digits=12, decimal_places=6)
    # The version the operator saw in the confirmation dialog; a stale value
    # is rejected so a double click/parallel request cannot add budget twice.
    budget_version: int = Field(ge=1)


@router.post("/api/v1/evaluations/prepare")
def prepare_evaluation(
    payload: PrepareRequest,
    session: Session = Depends(get_session),
) -> dict:
    """Run the M3-03 blocking/warning consistency checks without creating a run."""

    result = preflight_service.check_preparation(
        session,
        dataset_version_id=payload.dataset_version_id,
        model_config_id=payload.model_config_id,
        model_name=payload.model_name,
        runtime_key=payload.runtime_key,
        runtime_version=payload.runtime_version,
        resource_pool_id=payload.resource_pool_id,
        requested_concurrency=payload.requested_concurrency,
        scoring_image_digest=payload.scoring_image_digest,
        execution_image_digest=payload.execution_image_digest,
        assets_resolved=payload.assets_resolved,
    )
    return {"status": "ok", **result.payload()}


@router.post("/api/v1/evaluations")
def create_evaluation(
    payload: CreateEvaluationRequest,
    response: Response,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    settings = get_settings()
    requested_concurrency = (
        payload.requested_concurrency
        if payload.requested_concurrency is not None
        else settings.eval_default_requested_concurrency
    )
    scoring_image_digest = payload.scoring_image_digest
    execution_image_digest = payload.execution_image_digest
    if payload.dataset_version_id:
        if not (payload.model_config_id and payload.resource_pool_id):
            raise HTTPException(
                status_code=422,
                detail=error_detail(
                    "evaluation_invalid",
                    "真实评测必须指定模型配置与资源池",
                    details={"fields": {"model_config_id": "required", "resource_pool_id": "required"}},
                ),
            )
        check = preflight_service.check_preparation(
            session,
            dataset_version_id=payload.dataset_version_id,
            model_config_id=payload.model_config_id,
            model_name=payload.model_name,
            runtime_key=payload.runtime_key or "claude-code",
            runtime_version=payload.runtime_version,
            resource_pool_id=payload.resource_pool_id,
            requested_concurrency=requested_concurrency,
            scoring_image_digest=payload.scoring_image_digest,
            execution_image_digest=payload.execution_image_digest,
            assets_resolved=payload.assets_resolved,
        )
        if check.blockers:
            raise HTTPException(
                status_code=422,
                detail=error_detail(
                    "preparation_blocked",
                    "启动前一致性检查未通过",
                    details={"blockers": check.payload()["blockers"], "warnings": check.payload()["warnings"]},
                ),
            )
        # M5-07: freeze the digests resolved from request, dataset version or
        # platform defaults, not just the raw request payload.
        scoring_image_digest = check.scoring_image_digest
        execution_image_digest = check.execution_image_digest
    else:
        if not payload.case_keys:
            raise HTTPException(
                status_code=422,
                detail=error_detail("evaluation_invalid", "合成评测必须提供 case_keys"),
            )
    try:
        run, created = evaluation_service.create_synthetic_evaluation(
            session,
            name=payload.name or ("合成评测" if not payload.dataset_version_id else ""),
            case_keys=payload.case_keys or [],
            shard_size=payload.shard_size,
            requested_concurrency=requested_concurrency,
            idempotency_key=payload.idempotency_key,
            created_by=context.user.email,
            model_config_id=payload.model_config_id,
            model_name=payload.model_name,
            model_budget_usd=payload.model_budget_usd,
            dataset_version_id=payload.dataset_version_id,
            runtime_key=payload.runtime_key,
            runtime_version=payload.runtime_version,
            resource_pool_id=payload.resource_pool_id,
            scoring_image_digest=scoring_image_digest,
            execution_image_digest=execution_image_digest,
            consistency_json=check.payload() if payload.dataset_version_id else {},
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


@router.get("/api/v1/evaluations/{run_id}/events/stream")
def stream_evaluation_events(
    run_id: str,
    request: Request,
    after_id: Annotated[int | None, Query(ge=0)] = None,
    session: Session = Depends(get_session),
):
    """SSE change feed (M3-04, design 04 §6).

    The browser first fetches the DB snapshot + cursor via ``/events`` and then
    connects here with ``Last-Event-ID`` (or ``after_id``). The server replays
    from the DB; a terminal run ends the stream after its final event.
    """

    try:
        evaluation_service.get_evaluation(session, run_id)
    except evaluation_service.EvaluationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("evaluation_not_found", "评测不存在"),
        ) from exc
    header_cursor = request.headers.get("last-event-id")
    try:
        cursor = after_id if after_id is not None else int(header_cursor or 0)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=error_detail("invalid_cursor", "Last-Event-ID 不是有效游标"),
        ) from exc

    stream_factory = sessionmaker(
        bind=session.get_bind(), autoflush=False, expire_on_commit=False, class_=Session
    )

    def generate() -> Iterator[str]:
        position = cursor
        for _ in range(3600):
            with stream_factory() as stream_session:
                items, _next = evaluation_service.list_events(
                    stream_session, run_id, after_id=position, limit=100
                )
                run = evaluation_service.get_evaluation(stream_session, run_id)
                terminal = run.status in evaluation_service.TERMINAL_RUN_STATUSES
            for item in items:
                position = item["id"]
                yield (
                    f"id: {item['id']}\n"
                    f"event: {item['event_type']}\n"
                    f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
                )
            if terminal and not items:
                return
            time.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@router.post("/api/v1/evaluations/{run_id}/resume-budget")
def resume_budget(
    run_id: str,
    payload: ResumeBudgetRequest,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    """M4-05: add budget to a paused evaluation and re-dispatch unfinished cases."""

    try:
        run, attempts_created = budget_service.resume_budget(
            session,
            run_id=run_id,
            additional_budget_usd=payload.additional_budget_usd,
            expected_budget_version=payload.budget_version,
            actor_user_id=context.user.id,
            settings=get_settings(),
        )
    except evaluation_service.EvaluationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("evaluation_not_found", "评测不存在"),
        ) from exc
    except budget_service.BudgetPauseInProgress as exc:
        raise HTTPException(
            status_code=409,
            detail=error_detail("budget_pause_in_progress", str(exc)),
        ) from exc
    except budget_service.BudgetVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "budget_version_conflict",
                str(exc),
                details={"current_budget_version": exc.current_version},
            ),
        ) from exc
    except budget_service.BudgetNotPaused as exc:
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "evaluation_not_budget_paused",
                str(exc),
                details={"status": exc.status},
            ),
        ) from exc
    except budget_service.BudgetInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail=error_detail("evaluation_invalid", str(exc)),
        ) from exc
    except evaluation_service.EvaluationInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail=error_detail(
                "evaluation_invalid",
                str(exc),
                details={"fields": {exc.field: "invalid"}} if exc.field else None,
            ),
        ) from exc
    spent = evaluation_service.spent_usd(session, run.id)
    return {
        "resumed": True,
        "attempts_created": attempts_created,
        **evaluation_service.evaluation_summary(run, spent_usd=spent),
    }


def _run_body(summary: dict, created: bool) -> dict:
    return {"created": created, **summary}

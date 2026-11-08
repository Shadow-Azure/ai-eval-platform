"""Runner plane API (M2-04).

Routes consumed by the deployed fixed-runner heartbeat agent:

- POST /runner/v1/runners/heartbeat          — agent liveness + busy state
- GET  /api/v1/runners, /api/v1/runners/{id} — console queries (status/labels/heartbeat/task)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..runner_contracts import ContractInvalid, validate_payload
from ..services import runners as runner_service
from .errors import error_detail

router = APIRouter(tags=["runners"])


class HeartbeatRequest(BaseModel):
    runner_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    labels: list[str] = Field(default_factory=list, max_length=64)
    status: Literal["online", "busy", "offline"] = "online"
    current_case_attempt_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _validate_attempt_binding(self) -> HeartbeatRequest:
        if self.status == "busy" and not self.current_case_attempt_id:
            raise ValueError("busy heartbeat 需要 current_case_attempt_id")
        if self.status != "busy" and self.current_case_attempt_id:
            raise ValueError("仅 busy 心跳可携带 current_case_attempt_id")
        return self


def require_runner_token(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Bearer RUNNER_HEARTBEAT_TOKEN check; open when no token is configured."""

    expected = settings.runner_heartbeat_token
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=401,
            detail=error_detail("runner_token_invalid", "Runner 令牌无效"),
        )


@router.post("/runner/v1/runners/heartbeat", dependencies=[Depends(require_runner_token)])
def runner_heartbeat(
    payload: HeartbeatRequest,
    session: Session = Depends(get_session),
) -> dict:
    try:
        runner = runner_service.record_runner_heartbeat(
            session,
            runner_id=payload.runner_id,
            name=payload.name,
            labels=payload.labels,
            status=payload.status,
            current_case_attempt_id=payload.current_case_attempt_id,
        )
    except runner_service.RunnerBusyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "runner_busy_conflict",
                "同一 Runner 同时只能执行一个 Job",
                details={
                    "bound_case_attempt_id": exc.runner.busy_case_attempt_id,
                    "incoming_case_attempt_id": exc.incoming_attempt,
                },
            ),
        ) from exc
    return {
        "ok": True,
        "status": runner.status,
        "runner_id": runner.provider_runner_id,
        "last_heartbeat_at": runner_service.runner_payload(runner)["last_heartbeat_at"],
    }


@router.get("/api/v1/runners")
def list_runners(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    runner_service.sweep_stale_runners(
        session, timeout_seconds=settings.runner_heartbeat_timeout_seconds
    )
    runners = [
        runner_service.runner_payload(runner)
        for runner in runner_service.list_runners(session)
    ]
    return {"status": "ok", "runners": runners}


@router.get("/api/v1/runners/{provider_runner_id}")
def get_runner(
    provider_runner_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    runner_service.sweep_stale_runners(
        session, timeout_seconds=settings.runner_heartbeat_timeout_seconds
    )
    try:
        runner = runner_service.get_runner_by_provider_id(session, provider_runner_id)
    except runner_service.RunnerNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("runner_not_found", "Runner 不存在"),
        ) from exc
    return runner_service.runner_payload(runner)


# --------------------------------------------------------------------------
# Case-contract callbacks (runner-contract/schemas/v1)
#
# The synthetic-task workflow POSTs heartbeat/uploads/completed/failed to
# /runner/v1/case-runs/{case_run_id}/{action}. M2-04 folds them into runner
# state (busy binding / release) and validates payloads against the v1
# schemas; full attempt/dispatch persistence arrives with M2-05.
# --------------------------------------------------------------------------

CASE_ACTION_KINDS: dict[str, str] = {
    "heartbeat": "heartbeat",
    "uploads": "upload",
    "completed": "completion",
    "failed": "failure",
}


def _runner_conflict(exc: runner_service.RunnerBusyConflict) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=error_detail(
            "runner_busy_conflict",
            "同一 Runner 同时只能执行一个 Job",
            details={
                "bound_case_attempt_id": exc.runner.busy_case_attempt_id,
                "incoming_case_attempt_id": exc.incoming_attempt,
            },
        ),
    )


@router.post(
    "/runner/v1/case-runs/{case_run_id}/{action}",
    dependencies=[Depends(require_runner_token)],
)
def case_callback(
    case_run_id: str,
    action: Literal["heartbeat", "uploads", "completed", "failed"],
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
    x_runner_id: Annotated[str | None, Header()] = None,
) -> dict:
    kind = CASE_ACTION_KINDS[action]
    try:
        validate_payload(kind, payload)
    except ContractInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail=error_detail("contract_invalid", f"{kind} 校验失败：{'; '.join(exc.errors)}"),
        ) from exc

    if payload.get("case_run_id") != case_run_id:
        raise HTTPException(
            status_code=422,
            detail=error_detail(
                "contract_invalid",
                f"payload.case_run_id ({payload.get('case_run_id')}) 与路径 case_run_id ({case_run_id}) 不一致",
            ),
        )

    runner = None
    if x_runner_id:
        try:
            runner = runner_service.get_runner_by_provider_id(session, x_runner_id)
        except runner_service.RunnerNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail=error_detail("runner_not_found", "Runner 不存在"),
            ) from exc
        if action in ("heartbeat", "uploads"):
            try:
                runner_service.bind_case(session, runner=runner, case_run_id=case_run_id)
            except runner_service.RunnerBusyConflict as exc:
                raise _runner_conflict(exc) from exc
        else:
            runner_service.release_case(session, runner=runner)

    return {
        "ok": True,
        "case_run_id": case_run_id,
        "runner_id": runner.provider_runner_id if runner is not None else None,
        "status": runner.status if runner is not None else None,
    }

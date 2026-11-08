from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..models import ResourcePool, Runner, new_id

router = APIRouter(tags=["runners"])

DEFAULT_POOL_NAME = "default-runner-pool"


class RunnerHeartbeat(BaseModel):
    runner_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    labels: list[str] = Field(default_factory=list, max_length=64)
    status: Literal["online", "busy", "offline"] = "online"
    current_case_attempt_id: str | None = Field(default=None, max_length=64)


def require_runner_token(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    expected = settings.runner_heartbeat_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "runner_token_not_configured",
                    "message": "平台尚未配置 Runner 心跳令牌",
                    "request_id": None,
                }
            },
        )
    token = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else ""
    if not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "runner_token_invalid",
                    "message": "Runner 心跳令牌无效",
                    "request_id": None,
                }
            },
        )


def _ensure_default_pool(session: Session) -> ResourcePool:
    pool = session.scalar(
        select(ResourcePool).where(
            ResourcePool.tenant_id == str(DEFAULT_TENANT_ID),
            ResourcePool.name == DEFAULT_POOL_NAME,
        )
    )
    if pool is None:
        pool = ResourcePool(
            id=new_id(),
            tenant_id=str(DEFAULT_TENANT_ID),
            project_id=str(DEFAULT_PROJECT_ID),
            name=DEFAULT_POOL_NAME,
            provider_key="github-self-hosted",
            runner_group="default",
            labels_json={"labels": ["self-hosted", "linux", "eval"]},
            status="active",
        )
        session.add(pool)
        session.flush()
    return pool


@router.post("/runner/v1/runners/heartbeat")
def heartbeat(
    payload: RunnerHeartbeat,
    session: Session = Depends(get_session),
    _: None = Depends(require_runner_token),
) -> dict[str, object]:
    now = datetime.now(UTC)
    pool = _ensure_default_pool(session)
    runner = session.scalar(
        select(Runner).where(
            Runner.resource_pool_id == pool.id,
            Runner.provider_runner_id == payload.runner_id,
        )
    )
    if runner is None:
        runner = Runner(
            id=new_id(),
            tenant_id=str(DEFAULT_TENANT_ID),
            resource_pool_id=pool.id,
            provider_runner_id=payload.runner_id,
            name=payload.name,
            labels_json={"labels": payload.labels},
            status=payload.status,
            registered_at=now,
        )
        session.add(runner)
    runner.name = payload.name
    runner.labels_json = {"labels": payload.labels}
    runner.status = payload.status
    runner.last_heartbeat_at = now
    runner.busy_case_attempt_id = (
        payload.current_case_attempt_id if payload.status == "busy" else None
    )
    session.commit()
    return {
        "status": "ok",
        "runner_id": runner.id,
        "provider_runner_id": runner.provider_runner_id,
        "server_time": now.isoformat(),
    }


@router.get("/api/v1/runners")
def list_runners(session: Session = Depends(get_session)) -> dict[str, object]:
    settings = get_settings()
    threshold = datetime.now(UTC) - timedelta(
        seconds=settings.runner_heartbeat_stale_seconds
    )
    runners = session.scalars(
        select(Runner).where(Runner.tenant_id == str(DEFAULT_TENANT_ID)).order_by(Runner.name)
    ).all()
    items = []
    online_count = 0
    for runner in runners:
        last = runner.last_heartbeat_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        online = (
            runner.status in {"online", "busy"} and last is not None and last >= threshold
        )
        effective_status = runner.status if online else "offline"
        if online:
            online_count += 1
        items.append(
            {
                "id": runner.id,
                "name": runner.name,
                "labels": (runner.labels_json or {}).get("labels", []),
                "status": effective_status,
                "last_heartbeat_at": last.isoformat() if last is not None else None,
                "current_case_attempt_id": runner.busy_case_attempt_id,
                "resource_pool_id": runner.resource_pool_id,
            }
        )
    return {
        "items": items,
        "counts": {"online": online_count, "total": len(items)},
        "stale_after_seconds": settings.runner_heartbeat_stale_seconds,
    }

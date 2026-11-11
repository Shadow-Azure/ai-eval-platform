"""Delivery pipeline console API (M5-18).

``GET /api/v1/deployments`` is readable by every authenticated member: it
returns deployment rows written by the GitHub Actions workflow, the current and
previous successful versions, a compact component-health summary and whether a
controlled rollback is currently possible.

``POST /api/v1/deployments/{id}/rollback`` is administrator-only, requires an
explicit ``confirm`` flag and only accepts the immediately previous successful
deployment whose migration revision still matches the live database. The
request is audit-logged and dispatched to the deploy workflow through
``repository_dispatch``; the API does not SSH into the host and never performs
a database downgrade.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..models import Deployment
from ..services import deployments as deployment_service
from ..services import identity
from ..services.github_dispatch import GitHubDispatchError
from .dependencies import require_admin, require_session
from .errors import error_detail

router = APIRouter(tags=["deployments"])


class RollbackRequest(BaseModel):
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


def _http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=error_detail(code, message, details=details),
    )


def _previous_succeeded(
    session: Session, environment: str, current: Deployment
) -> Deployment | None:
    statement = (
        select(Deployment)
        .where(
            Deployment.environment == environment,
            Deployment.status == "succeeded",
            Deployment.id != current.id,
        )
        .order_by(Deployment.started_at.desc(), Deployment.id.desc())
        .limit(1)
    )
    return session.scalar(statement)


@router.get("/api/v1/deployments")
def list_deployments(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    _context: Annotated[identity.AuthContext, Depends(require_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    return deployment_service.deployments_payload(session, settings, limit=limit)


@router.post(
    "/api/v1/deployments/{deployment_id}/rollback",
    status_code=status.HTTP_202_ACCEPTED,
)
def rollback_deployment(
    deployment_id: str,
    body: RollbackRequest,
    context: Annotated[identity.AuthContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    target = deployment_service.get_deployment(session, deployment_id)
    if target is None or target.environment != settings.deploy_environment:
        raise _http_error(404, "deployment_not_found", "部署记录不存在")

    current = session.scalar(
        select(Deployment)
        .where(
            Deployment.environment == settings.deploy_environment,
            Deployment.status == "succeeded",
        )
        .order_by(Deployment.started_at.desc(), Deployment.id.desc())
        .limit(1)
    )
    if current is None:
        raise _http_error(409, "rollback_unavailable", "还没有成功部署记录，无法回滚")
    previous = _previous_succeeded(session, settings.deploy_environment, current)
    if previous is None or previous.id != target.id:
        raise _http_error(
            409,
            "rollback_target_changed",
            "回滚目标已变化，请刷新页面后重新选择上一版本。",
            details={"expected_target_id": previous.id if previous else None},
        )

    db_revision = deployment_service.database_revision(session)
    state = deployment_service.rollback_state(
        current=current,
        previous=previous,
        db_revision=db_revision,
        settings=settings,
    )
    if not state["available"]:
        code = (
            "github_not_configured"
            if state["reason"] == "github_not_configured"
            else "rollback_unavailable"
        )
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE if code == "github_not_configured" else 409,
            code,
            state["message"],
            details={"reason": state["reason"]},
        )

    if not body.confirm:
        raise _http_error(
            422,
            "confirmation_required",
            "回滚需要二次确认，请勾选确认项后重试。",
        )

    try:
        payload = deployment_service.dispatch_rollback(
            settings=settings,
            target=target,
            current=current,
            requested_by=context.user.email,
            reason=body.reason,
        )
    except GitHubDispatchError as exc:
        raise _http_error(
            status.HTTP_502_BAD_GATEWAY,
            "github_dispatch_failed",
            f"回滚工作流派发失败：{exc.message}",
        ) from exc

    identity.record_audit(
        session,
        tenant_id=context.tenant.id,
        actor_user_id=context.user.id,
        action="deployment.rollback_requested",
        target_type="deployment",
        target_id=target.id,
        metadata={
            "current_deployment_id": current.id,
            "target_git_commit": target.git_commit,
            "current_git_commit": current.git_commit,
            "database_revision": db_revision,
            "requested_by": context.user.email,
            "reason": body.reason or "",
            "event_type": payload.get("event_type"),
        },
    )
    session.commit()

    item = deployment_service.deployment_item(target, settings, {})
    return {
        "requested": True,
        "target": item,
        "dispatched_at": payload.get("dispatched_at"),
        "actions_url": deployment_service.github_links(settings)["actions_url"],
    }

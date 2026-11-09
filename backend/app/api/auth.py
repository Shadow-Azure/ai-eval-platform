"""Console authentication API (M3-05, design 04 Auth row).

- ``POST /api/v1/auth/login``  — password login, sets the HttpOnly session cookie
- ``POST /api/v1/auth/logout`` — revokes the current session and clears the cookie
- ``GET  /api/v1/auth/me``     — current user, tenant, workspace and role

Login failures intentionally share one generic 401 response so the endpoint
does not reveal whether an account exists or is disabled.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..services import identity
from . import dependencies
from .errors import error_detail

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    remember: bool = False


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=error_detail("invalid_credentials", "账号或密码不正确。"),
    )


def _cookie_options(settings: Settings) -> dict[str, object]:
    return {
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }


@router.post("/api/v1/auth/login")
def login(
    payload: LoginRequest,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    result = identity.authenticate(session, payload.email, payload.password)
    if result is None:
        raise _invalid_credentials()
    user, membership = result
    context = identity.build_context(session, user, membership)
    if context is None:
        raise _invalid_credentials()

    auth_session, token, _expires_at = identity.create_session(
        session,
        user=user,
        membership=membership,
        settings=settings,
        remember=payload.remember,
    )
    identity.record_audit(
        session,
        tenant_id=membership.tenant_id,
        actor_user_id=user.id,
        action="auth.login",
        target_type="auth_session",
        target_id=auth_session.id,
        metadata={"remember": payload.remember},
    )
    session.commit()

    ttl_seconds = (
        settings.auth_session_remember_days * 24 * 60 * 60
        if payload.remember
        else settings.auth_session_ttl_hours * 60 * 60
    )
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=max(0, ttl_seconds),
        **_cookie_options(settings),
    )
    return context.as_payload()


@router.post("/api/v1/auth/logout")
def logout(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[identity.AuthContext | None, Depends(dependencies.optional_session)],
) -> dict[str, str]:
    token = request.cookies.get(settings.auth_cookie_name)
    revoked = identity.revoke_session_by_token(
        session, settings=settings, token=token, now=datetime.now(UTC)
    )
    if context is not None and revoked is not None:
        identity.record_audit(
            session,
            tenant_id=context.membership.tenant_id,
            actor_user_id=context.user.id,
            action="auth.logout",
            target_type="auth_session",
            target_id=revoked.id,
        )
    session.commit()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        **_cookie_options(settings),
    )
    return {"status": "ok"}


@router.get("/api/v1/auth/me")
def me(
    context: Annotated[identity.AuthContext, Depends(dependencies.require_session)],
) -> dict[str, object]:
    return context.as_payload()

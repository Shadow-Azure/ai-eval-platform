"""FastAPI dependencies for console session and role enforcement."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..services import identity
from .errors import error_detail

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _unauthenticated(message: str = "登录已失效，请重新登录。") -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=error_detail("unauthenticated", message),
    )


def _resolve(
    request: Request,
    session: Session,
    settings: Settings,
) -> identity.AuthContext | None:
    token = request.cookies.get(settings.auth_cookie_name)
    return identity.resolve_session(session, settings=settings, token=token)


def require_session(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> identity.AuthContext:
    context = _resolve(request, session, settings)
    if context is None:
        raise _unauthenticated()
    request.state.auth = context
    return context


def optional_session(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> identity.AuthContext | None:
    context = _resolve(request, session, settings)
    if context is not None:
        request.state.auth = context
    return context


def require_admin(
    context: Annotated[identity.AuthContext, Depends(require_session)],
) -> identity.AuthContext:
    if context.role != identity.ROLE_ADMIN:
        raise HTTPException(
            status_code=403,
            detail=error_detail("forbidden", "当前账号没有写入权限。"),
        )
    return context

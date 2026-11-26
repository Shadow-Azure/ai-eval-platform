"""FastAPI dependencies for console session and role enforcement.

M10 (#429) adds the machine-identity path: ``require_session_or_service``
accepts either the console session cookie or ``Authorization: Bearer hev_...``.
A service token must carry the scope the route declares and, for launches, the
requested template must be inside the token's allowlist. A present but
malformed ``Authorization`` header is always a 401 — it never silently falls
back to the cookie, so a caller cannot mistake a broken machine credential for
a working console session.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..models import SERVICE_TOKEN_SCOPES, ServiceToken
from ..services import identity
from ..services import service_tokens as token_service
from .errors import error_detail

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

SCOPE_EVAL_LAUNCH = "eval:launch"
SCOPE_EVAL_READ = "eval:read"


@dataclass(frozen=True)
class Actor:
    """The authenticated principal of a stable execution API call."""

    label: str
    is_admin: bool
    is_service: bool
    scopes: frozenset[str] = frozenset()
    service_token: ServiceToken | None = None


def _unauthenticated(message: str = "登录已失效，请重新登录。") -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=error_detail("unauthenticated", message),
    )


def _forbidden(message: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=error_detail("forbidden", message),
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


def _service_actor(
    request: Request,
    session: Session,
    header: str,
    *,
    scope: str,
) -> Actor:
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _unauthenticated("服务令牌格式不正确，请使用 Bearer hev_... 认证。")
    raw_token = parts[1].strip()
    if not raw_token.startswith(token_service.TOKEN_PREFIX):
        raise _unauthenticated("服务令牌格式不正确，请使用 Bearer hev_... 认证。")
    token = token_service.authenticate(session, raw_token)
    if token is None:
        raise _unauthenticated("服务令牌无效、已撤销或已过期。")
    scopes = frozenset(str(item) for item in (token.scopes_json or []))
    if scope not in scopes:
        raise _forbidden("服务令牌缺少所需权限。")
    if scope == SCOPE_EVAL_LAUNCH:
        allowlist = [str(item) for item in (token.template_allowlist_json or [])]
        if allowlist:
            template_id = str(request.path_params.get("template_id") or "")
            if template_id not in allowlist:
                raise _forbidden("服务令牌无权启动该模板。")
    return Actor(
        label=token_service.actor_label(token),
        is_admin=False,
        is_service=True,
        scopes=scopes,
        service_token=token,
    )


def require_session_or_service(scope: str) -> Callable[..., Actor]:
    """Build the dependency for a stable execution route.

    ``scope`` is one of ``SERVICE_TOKEN_SCOPES``: ``eval:launch`` additionally
    requires the admin role for a console session, while ``eval:read`` accepts
    any authenticated role.
    """

    if scope not in SERVICE_TOKEN_SCOPES:
        raise ValueError(f"unknown service token scope: {scope}")

    def dependency(
        request: Request,
        session: SessionDep,
        settings: SettingsDep,
    ) -> Actor:
        header = request.headers.get("Authorization")
        if header is not None:
            return _service_actor(request, session, header, scope=scope)
        context = _resolve(request, session, settings)
        if context is None:
            raise _unauthenticated()
        request.state.auth = context
        if scope == SCOPE_EVAL_LAUNCH and context.role != identity.ROLE_ADMIN:
            raise _forbidden("当前账号没有写入权限。")
        return Actor(
            label=context.user.email,
            is_admin=context.role == identity.ROLE_ADMIN,
            is_service=False,
        )

    return dependency

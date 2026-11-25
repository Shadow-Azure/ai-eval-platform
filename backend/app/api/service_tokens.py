"""M10 (#429): admin API for service tokens (machine identity).

Creating, listing and revoking tokens stays a console administrator action, so
every route depends on :func:`require_admin`. The plaintext is returned exactly
once by ``POST``; ``GET`` only exposes metadata and ``DELETE`` revokes (never
deletes) so a revoked token keeps its audit trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..services import identity
from ..services import service_tokens as token_service
from .dependencies import require_admin
from .errors import error_detail

router = APIRouter(tags=["service-tokens"])


class ServiceTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Literal["eval:launch", "eval:read"]] = Field(min_length=1)
    template_allowlist: list[str] = Field(default_factory=list)
    max_concurrent_runs: int | None = Field(default=None, ge=1)
    daily_budget_cny: float | None = Field(default=None, ge=0)
    expires_at: datetime | None = None


def _http_error(error: token_service.ServiceTokenError) -> HTTPException:
    details = dict(error.details or {})
    if error.field:
        fields = dict(details.get("fields") or {})
        fields[error.field] = "invalid"
        details["fields"] = fields
    return HTTPException(
        status_code=error.status,
        detail=error_detail(error.code, str(error), details=details or None),
    )


@router.post("/api/v1/service-tokens", status_code=201)
def create_service_token(
    payload: ServiceTokenCreateRequest,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    """Create a token; the plaintext is returned here and never again."""

    try:
        token, plaintext = token_service.create_token(
            session,
            payload.model_dump(),
            created_by=context.user.email,
        )
    except token_service.ServiceTokenError as error:
        raise _http_error(error) from error
    return {
        "status": "ok",
        "service_token": token_service.token_view(token),
        "token": plaintext,
    }


@router.get("/api/v1/service-tokens")
def list_service_tokens(
    session: Session = Depends(get_session),
    _context: identity.AuthContext = Depends(require_admin),
) -> dict:
    tokens = token_service.list_tokens(session)
    return {
        "status": "ok",
        "items": [token_service.token_view(token) for token in tokens],
    }


@router.delete("/api/v1/service-tokens/{token_id}", status_code=204)
def revoke_service_token(
    token_id: str,
    session: Session = Depends(get_session),
    _context: identity.AuthContext = Depends(require_admin),
) -> Response:
    try:
        token_service.revoke_token(session, token_id)
    except token_service.ServiceTokenError as error:
        raise _http_error(error) from error
    return Response(status_code=204)

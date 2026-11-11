"""Trace query endpoints (M6-04)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..defaults import DEFAULT_PROJECT_ID
from ..models import Project
from ..models.trace import TRACE_SPAN_KINDS
from ..services import identity, trace_queries
from ..services.object_store import object_store_from_settings
from ..services.trace_redaction import redact_text
from .dependencies import require_admin, require_session
from .errors import error_detail

router = APIRouter(tags=["traces"])


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=error_detail(code, message),
    )


def _scope(session: Session, context: identity.AuthContext) -> tuple[str, str]:
    project = session.scalar(
        select(Project).where(
            Project.id == str(DEFAULT_PROJECT_ID),
            Project.tenant_id == context.tenant.id,
            Project.workspace_id == context.membership.workspace_id,
        )
    )
    if project is None:
        raise _error(404, "project_not_found", "项目不存在")
    return context.tenant.id, project.id


@router.get("/api/v1/eval-case-runs/{case_run_id}/trace")
def get_attempt_trace(
    case_run_id: str,
    attempt: Annotated[int | None, Query(ge=1)] = None,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_session),
) -> dict[str, object]:
    tenant_id, project_id = _scope(session, context)
    try:
        return trace_queries.attempt_trace(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            case_run_id=case_run_id,
            attempt_no=attempt,
        )
    except trace_queries.TraceQueryError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.get("/api/v1/trace-sessions/{session_id}/spans")
def get_session_spans(
    session_id: str,
    cursor: str | None = Query(default=None, max_length=512),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    kind: str | None = Query(default=None),
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_session),
) -> dict[str, object]:
    tenant_id, project_id = _scope(session, context)
    if kind is not None and kind not in TRACE_SPAN_KINDS:
        raise _error(422, "invalid_span_kind", "轨迹节点类型无效")
    try:
        return trace_queries.session_spans(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            session_id=session_id,
            role=context.role,
            cursor=cursor,
            limit=limit,
            kind=kind,
        )
    except trace_queries.TraceQueryError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.get("/api/v1/trace-spans/{span_id}")
def get_trace_span(
    span_id: str,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_session),
) -> dict[str, object]:
    tenant_id, project_id = _scope(session, context)
    try:
        return trace_queries.span_detail(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            span_id=span_id,
            role=context.role,
        )
    except trace_queries.TraceQueryError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.post("/api/v1/trace-files/{file_id}/download-url")
def create_trace_file_download_url(
    file_id: str,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    context: identity.AuthContext = Depends(require_admin),
) -> dict[str, object]:
    tenant_id, project_id = _scope(session, context)
    try:
        trace_file = trace_queries.trace_file_for_download(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            file_id=file_id,
        )
    except trace_queries.TraceQueryError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    store = object_store_from_settings(settings)
    if store is None:
        raise _error(
            503,
            "object_store_unavailable",
            "对象存储未配置，无法签发轨迹原件下载地址",
        )
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=300)
    url = store.presign_get_url(trace_file.object_key, expires=300, now=now)
    identity.record_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=context.user.id,
        action="trace.file.download_url",
        target_type="trace_file",
        target_id=trace_file.id,
        metadata={
            "result": "issued",
            "expires_at": expires_at.isoformat(),
            "sha256": trace_file.sha256,
            "size": trace_file.size,
        },
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return {
        "url": url,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "file": {
            "id": trace_file.id,
            "logical_path": (
                redact_text(trace_file.logical_path, limit=512)
                if trace_file.logical_path
                else None
            ),
            "sha256": trace_file.sha256,
            "size": trace_file.size,
            "native_format": trace_file.native_format,
            "parser_version": trace_file.parser_version,
            "status": trace_file.status,
        },
    }


@router.post("/api/v1/trace-spans/{span_id}/content-url")
def create_trace_span_content_url(
    span_id: str,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    context: identity.AuthContext = Depends(require_admin),
) -> dict[str, object]:
    tenant_id, project_id = _scope(session, context)
    try:
        span = trace_queries.span_content_for_download(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            span_id=span_id,
        )
    except trace_queries.TraceQueryError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    store = object_store_from_settings(settings)
    if store is None:
        raise _error(
            503,
            "object_store_unavailable",
            "对象存储未配置，无法签发轨迹内容地址",
        )
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=300)
    url = store.presign_get_url(span.content_object_key, expires=300, now=now)
    identity.record_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=context.user.id,
        action="trace.span.content_url",
        target_type="trace_span",
        target_id=span.id,
        metadata={
            "result": "issued",
            "expires_at": expires_at.isoformat(),
            "session_id": span.session_id,
            "kind": span.kind,
        },
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return {
        "url": url,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "span": {
            "id": span.id,
            "session_id": span.session_id,
            "kind": span.kind,
            "native_event_id": span.native_event_id,
        },
    }

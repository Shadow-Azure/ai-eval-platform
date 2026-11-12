"""Agent asset source and snapshot console API (M5-01, design 04 §2 Assets).

Reads are available to every authenticated member; mutations (create source,
scan, resolve snapshot) require the administrator role. Git tokens are
write-only: responses expose a mask and never the plaintext.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..defaults import DEFAULT_TENANT_ID
from ..models import AgentAssetSnapshot
from ..services import agent_assets as asset_service
from ..services import identity
from ..services.git_client import GitClientError
from .dependencies import require_admin
from .errors import error_detail

router = APIRouter(tags=["agent-assets"])


class CreateSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repo_url: str = Field(min_length=1, max_length=500)
    default_ref: str = Field(default="main", min_length=1, max_length=200)
    scan_paths: list[str] = Field(default_factory=list, max_length=20)
    selection_mode: str = Field(default="directory", pattern="^(directory|explicit)$")
    description: str | None = Field(default=None, max_length=1000)
    credential_id: str | None = Field(default=None, max_length=36)
    access_token: str | None = Field(default=None, max_length=1000)
    runtime_prerequisites: dict[str, Any] = Field(default_factory=dict)
    # M8-10: optional token from POST /agent-asset-sources/load-test-preview.
    # Only the server-side cached result is persisted, never client fields.
    load_test_token: str | None = Field(default=None, max_length=128)


class LoadTestPreviewRequest(BaseModel):
    repo_url: str = Field(min_length=1, max_length=500)
    requested_ref: str = Field(default="main", min_length=1, max_length=200)
    scan_paths: list[str] = Field(default_factory=list, max_length=20)
    runtime_prerequisites: dict[str, Any] = Field(default_factory=dict)


class ScanRequest(BaseModel):
    requested_ref: str | None = Field(default=None, max_length=200)


class PreviewScanRequest(BaseModel):
    repo_url: str = Field(min_length=1, max_length=500)
    requested_ref: str = Field(default="main", min_length=1, max_length=200)
    scan_paths: list[str] = Field(default_factory=list, max_length=20)
    credential_id: str | None = Field(default=None, max_length=36)
    access_token: str | None = Field(default=None, max_length=1000)


class ResolveRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=36)
    requested_ref: str | None = Field(default=None, max_length=200)
    paths: list[str] = Field(min_length=1, max_length=500)
    types: dict[str, str] = Field(default_factory=dict)
    runtime_key: str = Field(default="claude-code", max_length=64)


def _http_error(error: asset_service.AgentAssetError | GitClientError) -> HTTPException:
    details: dict[str, Any] = {}
    if isinstance(error, asset_service.AgentAssetError):
        if error.field:
            details["fields"] = {error.field: "invalid"}
        if error.details:
            details["context"] = error.details
        return HTTPException(
            status_code=error.status,
            detail=error_detail(error.code, str(error), details=details or None),
        )
    status = error.status or 422
    if status >= 500:
        status = 502
    return HTTPException(
        status_code=status,
        detail=error_detail(error.code, str(error)),
    )


def _source_body(session: Session, source, settings: Settings) -> dict[str, Any]:
    mask = asset_service.source_credential_mask(session, source)
    return asset_service.source_dict(
        source,
        credential_mask=mask,
        uses_platform_token=not source.credential_id and bool(settings.github_token),
    )


def _scan_body(source_id: str | None, result: asset_service.ScanResult) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "requested_ref": result.requested_ref,
        "resolved_commit": result.resolved_commit,
        "assets": [asset.as_dict() for asset in result.assets],
        "unclassified": list(result.unclassified),
        "counts": asset_service.asset_counts(result.assets),
        "warnings": list(result.warnings),
    }


@router.get("/api/v1/agent-asset-sources")
def list_agent_asset_sources(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    rows, total = asset_service.list_sources(session, limit=limit, offset=offset)
    return {
        "items": [_source_body(session, source, settings) for source in rows],
        "page_info": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_more": offset + len(rows) < total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        },
    }


@router.post("/api/v1/agent-asset-sources")
def create_agent_asset_source(
    payload: CreateSourceRequest,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        source = asset_service.create_source(
            session,
            name=payload.name,
            repo_url=payload.repo_url,
            default_ref=payload.default_ref,
            scan_paths=payload.scan_paths,
            selection_mode=payload.selection_mode,
            description=payload.description,
            credential_id=payload.credential_id,
            access_token=payload.access_token,
            runtime_prerequisites=payload.runtime_prerequisites,
            load_test_token=payload.load_test_token,
            settings=settings,
        )
    except (asset_service.AgentAssetError, GitClientError) as error:
        raise _http_error(error) from error
    response.status_code = 201
    return _source_body(session, source, settings)


@router.get("/api/v1/agent-asset-sources/{source_id}")
def get_agent_asset_source(
    source_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        source = asset_service.get_source(session, source_id)
    except asset_service.AgentAssetError as error:
        raise _http_error(error) from error
    body = _source_body(session, source, settings)
    snapshot = session.scalar(
        select(AgentAssetSnapshot)
        .where(AgentAssetSnapshot.source_id == source.id)
        .order_by(AgentAssetSnapshot.created_at.desc(), AgentAssetSnapshot.id)
        .limit(1)
    )
    body["latest_snapshot_id"] = snapshot.id if snapshot is not None else None
    return body


@router.post("/api/v1/agent-asset-sources/load-test-preview")
def preview_agent_asset_load_test(
    payload: LoadTestPreviewRequest,
    _context: identity.AuthContext = Depends(require_admin),
) -> dict:
    """Run the CLI load test for the add-source wizard without persisting it."""

    try:
        result, token = asset_service.preview_load_test(
            repo_url=payload.repo_url,
            requested_ref=payload.requested_ref,
            scan_paths=payload.scan_paths,
            runtime_prerequisites=payload.runtime_prerequisites,
        )
    except (asset_service.AgentAssetError, GitClientError) as error:
        raise _http_error(error) from error
    return {"load_test_token": token, **asset_service.load_test_view(result)}


@router.post("/api/v1/agent-asset-sources/scan-preview")
def preview_agent_asset_scan(
    payload: PreviewScanRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        result = asset_service.preview_scan(
            session,
            repo_url=payload.repo_url,
            requested_ref=payload.requested_ref,
            scan_paths=payload.scan_paths,
            credential_id=payload.credential_id,
            access_token=payload.access_token,
            settings=settings,
        )
    except (asset_service.AgentAssetError, GitClientError) as error:
        raise _http_error(error) from error
    return _scan_body(None, result)


@router.post("/api/v1/agent-asset-sources/{source_id}/scan")
def scan_agent_asset_source(
    source_id: str,
    payload: ScanRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        result = asset_service.scan_source(
            session,
            source_id,
            requested_ref=payload.requested_ref,
            settings=settings,
        )
    except (asset_service.AgentAssetError, GitClientError) as error:
        raise _http_error(error) from error
    return _scan_body(source_id, result)


@router.post("/api/v1/agent-asset-sources/{source_id}/load-test")
def load_test_agent_asset_source(
    source_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        source, result = asset_service.run_load_test(session, source_id)
    except (asset_service.AgentAssetError, GitClientError) as error:
        raise _http_error(error) from error
    return {
        "source": _source_body(session, source, settings),
        "load_test": asset_service.load_test_view(result),
    }


@router.get("/api/v1/agent-asset-sources/{source_id}/tree")
def get_agent_asset_tree(
    source_id: str,
    session: Session = Depends(get_session),
) -> dict:
    try:
        source = asset_service.get_source(session, source_id)
    except asset_service.AgentAssetError as error:
        raise _http_error(error) from error
    return asset_service.asset_tree(source)


@router.post("/api/v1/agent-assets/resolve")
def resolve_agent_assets(
    payload: ResolveRequest,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        snapshot, assets, created = asset_service.resolve_assets(
            session,
            source_id=payload.source_id,
            requested_ref=payload.requested_ref,
            paths=payload.paths,
            types=payload.types,
            runtime_key=payload.runtime_key,
            created_by=context.user.email,
            settings=settings,
        )
    except (asset_service.AgentAssetError, GitClientError) as error:
        raise _http_error(error) from error
    response.status_code = 201 if created else 200
    return {
        "created": created,
        "snapshot": asset_service.snapshot_dict(snapshot, assets),
    }


@router.get("/api/v1/agent-asset-snapshots")
def list_agent_asset_snapshots(
    source_id: Annotated[str | None, Query(max_length=36)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
) -> dict:
    query = select(AgentAssetSnapshot).where(
        AgentAssetSnapshot.tenant_id == str(DEFAULT_TENANT_ID)
    )
    if source_id:
        query = query.where(AgentAssetSnapshot.source_id == source_id)
    rows = list(
        session.scalars(
            query.order_by(AgentAssetSnapshot.created_at.desc(), AgentAssetSnapshot.id)
            .limit(limit)
            .offset(offset)
        )
    )
    total_query = select(func.count()).select_from(AgentAssetSnapshot).where(
        AgentAssetSnapshot.tenant_id == str(DEFAULT_TENANT_ID)
    )
    if source_id:
        total_query = total_query.where(AgentAssetSnapshot.source_id == source_id)
    total = int(session.scalar(total_query) or 0)
    return {
        "items": [
            asset_service.snapshot_dict(
                row, asset_service.snapshot_assets(session, row.id)
            )
            for row in rows
        ],
        "page_info": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_more": offset + len(rows) < total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        },
    }


@router.get("/api/v1/agent-asset-snapshots/{snapshot_id}")
def get_agent_asset_snapshot(
    snapshot_id: str,
    session: Session = Depends(get_session),
) -> dict:
    try:
        snapshot = asset_service.get_snapshot(session, snapshot_id)
    except asset_service.AgentAssetError as error:
        raise _http_error(error) from error
    return asset_service.snapshot_dict(
        snapshot, asset_service.snapshot_assets(session, snapshot.id)
    )

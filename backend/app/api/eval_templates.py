"""M10 (#378): evaluation template management and one-click launch API."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agent_timeout import MAX_AGENT_TIMEOUT_SECONDS, MIN_AGENT_TIMEOUT_SECONDS
from ..db import get_session
from ..services import eval_templates as template_service
from ..services import identity
from ..services import service_tokens as token_service
from .dependencies import Actor, require_admin, require_session, require_session_or_service
from .errors import error_detail
from .evaluations import CaseSelectionRequest, ExecutionPolicyRequest

router = APIRouter(tags=["eval-templates"])


class TemplateSelfEvolutionRequest(BaseModel):
    """M8 (#382): template-level bad-case reporting target.

    Field-level rules are validated in the template service so every failure
    maps to the same ``template_invalid`` envelope with
    ``details.fields.self_evolution = "invalid"`` as the rest of the launch
    selection. On update, ``token`` is optional: an omitted value keeps the
    stored ciphertext, while disabling clears it.
    """

    enabled: bool = False
    base_url: str | None = None
    workspace_id: str | None = None
    token: str | None = None


class TemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    dataset_version_id: str = Field(min_length=1, max_length=36)
    runtime_key: str = Field(default="claude-code", max_length=64)
    runtime_version: str | None = Field(default=None, max_length=64)
    asset_source_id: str | None = Field(default=None, max_length=36)
    asset_ref: str | None = Field(default=None, max_length=200)
    asset_paths: list[str] | None = None
    asset_types: dict[str, str] = Field(default_factory=dict)
    model_config_id: str = Field(min_length=1, max_length=36)
    model_name: str | None = Field(default=None, max_length=200)
    resource_pool_id: str = Field(min_length=1, max_length=36)
    case_selection: CaseSelectionRequest | None = None
    execution: ExecutionPolicyRequest | None = None
    prompt_suffix: str | None = Field(default=None, max_length=4000)
    max_turns: int | None = Field(default=None, ge=1, le=1000)
    agent_timeout_seconds: int | None = Field(
        default=None, ge=MIN_AGENT_TIMEOUT_SECONDS, le=MAX_AGENT_TIMEOUT_SECONDS
    )
    self_evolution: TemplateSelfEvolutionRequest | None = None
    enabled: bool = True


class TemplateUpdateRequest(BaseModel):
    """PATCH: only the fields present in the request are updated."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    dataset_version_id: str | None = Field(default=None, max_length=36)
    runtime_key: str | None = Field(default=None, max_length=64)
    runtime_version: str | None = Field(default=None, max_length=64)
    asset_source_id: str | None = Field(default=None, max_length=36)
    asset_ref: str | None = Field(default=None, max_length=200)
    asset_paths: list[str] | None = None
    asset_types: dict[str, str] | None = None
    model_config_id: str | None = Field(default=None, max_length=36)
    model_name: str | None = Field(default=None, max_length=200)
    resource_pool_id: str | None = Field(default=None, max_length=36)
    case_selection: CaseSelectionRequest | None = None
    execution: ExecutionPolicyRequest | None = None
    prompt_suffix: str | None = Field(default=None, max_length=4000)
    max_turns: int | None = Field(default=None, ge=1, le=1000)
    agent_timeout_seconds: int | None = Field(
        default=None, ge=MIN_AGENT_TIMEOUT_SECONDS, le=MAX_AGENT_TIMEOUT_SECONDS
    )
    self_evolution: TemplateSelfEvolutionRequest | None = None
    enabled: bool | None = None


class AssetOverrideRequest(BaseModel):
    """M10 (#429): optional candidate asset selection for a machine launch.

    Whole-field replacement semantics: a field that is absent inherits the
    template; a field that is present replaces the template value entirely (no
    merging). ``commit`` is the authoritative identity and ``ref`` is display
    and audit only, so a candidate is always pinned by commit.
    """

    repo_url: str | None = Field(default=None, max_length=500)
    ref: str | None = Field(default=None, max_length=200)
    commit: str | None = Field(default=None, pattern="^[0-9a-f]{40}$")
    asset_paths: list[str] | None = None
    asset_types: dict[str, str] | None = None


class LaunchLineageRequest(BaseModel):
    """Caller provenance stored on the run; never part of evaluation semantics."""

    evolutionRunId: str | None = Field(default=None, max_length=64)
    evolutionRoundId: str | None = Field(default=None, max_length=64)
    roundSeq: int | None = Field(default=None, ge=1)
    parentCommit: str | None = Field(default=None, max_length=40)


class LaunchTemplateRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=200)
    asset_override: AssetOverrideRequest | None = None
    lineage: LaunchLineageRequest | None = None


def _http_error(
    error: template_service.TemplateError | token_service.ServiceTokenError,
) -> HTTPException:
    details = dict(error.details or {})
    if error.field:
        fields = dict(details.get("fields") or {})
        fields[error.field] = "invalid"
        details["fields"] = fields
    return HTTPException(
        status_code=error.status,
        detail=error_detail(error.code, str(error), details=details or None),
    )


def _create_payload(payload: TemplateCreateRequest) -> dict:
    data = payload.model_dump()
    data["case_selection"] = (
        payload.case_selection.model_dump() if payload.case_selection is not None else None
    )
    data["execution"] = (
        payload.execution.model_dump() if payload.execution is not None else None
    )
    return data


def _update_payload(payload: TemplateUpdateRequest) -> dict:
    changes = payload.model_dump(include=payload.model_fields_set)
    if "case_selection" in changes and payload.case_selection is not None:
        changes["case_selection"] = payload.case_selection.model_dump()
    if "execution" in changes and payload.execution is not None:
        changes["execution"] = payload.execution.model_dump()
    return changes


@router.get("/api/v1/eval-templates")
def list_eval_templates(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    dataset_version_id: Annotated[str | None, Query(max_length=36)] = None,
    session: Session = Depends(get_session),
    _context: identity.AuthContext = Depends(require_session),
) -> dict:
    """Console management list (not one of the three execution APIs)."""

    try:
        templates, total = template_service.list_templates(
            session,
            limit=limit,
            offset=offset,
            q=q,
            status=status,
            dataset_version_id=dataset_version_id,
        )
    except template_service.TemplateError as error:
        raise _http_error(error) from error
    return {
        "status": "ok",
        "items": [
            template_service.template_view(
                session, template, include_recent=True, recent_limit=1
            )
            for template in templates
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/api/v1/eval-templates", status_code=201)
def create_eval_template(
    payload: TemplateCreateRequest,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        template = template_service.create_template(
            session, _create_payload(payload), created_by=context.user.email
        )
    except template_service.TemplateError as error:
        raise _http_error(error) from error
    return {
        "status": "ok",
        "template": template_service.template_view(session, template, include_recent=True),
    }


@router.get("/api/v1/eval-templates/{template_id}")
def get_eval_template(
    template_id: str,
    session: Session = Depends(get_session),
    _context: identity.AuthContext = Depends(require_session),
) -> dict:
    try:
        template = template_service.get_template(session, template_id)
    except template_service.TemplateError as error:
        raise _http_error(error) from error
    return {
        "status": "ok",
        "template": template_service.template_view(
            session, template, include_recent=True
        ),
    }


@router.patch("/api/v1/eval-templates/{template_id}")
def update_eval_template(
    template_id: str,
    payload: TemplateUpdateRequest,
    version: Annotated[int | None, Query(ge=1)] = None,
    session: Session = Depends(get_session),
    _context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        template = template_service.update_template(
            session,
            template_id,
            _update_payload(payload),
            expected_version=version,
        )
    except template_service.TemplateError as error:
        raise _http_error(error) from error
    return {
        "status": "ok",
        "template": template_service.template_view(session, template),
    }


@router.delete("/api/v1/eval-templates/{template_id}", status_code=204)
def delete_eval_template(
    template_id: str,
    session: Session = Depends(get_session),
    _context: identity.AuthContext = Depends(require_admin),
) -> Response:
    try:
        template_service.delete_template(session, template_id)
    except template_service.TemplateError as error:
        raise _http_error(error) from error
    return Response(status_code=204)


@router.post("/api/v1/eval-templates/{template_id}/launch", status_code=202)
def launch_eval_template(
    template_id: str,
    payload: LaunchTemplateRequest,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    session: Session = Depends(get_session),
    context: Actor = Depends(require_session_or_service("eval:launch")),
) -> dict:
    key = (idempotency_header or payload.idempotency_key or "").strip()
    if not key:
        key = str(uuid.uuid4())
    asset_override = (
        payload.asset_override.model_dump(exclude_unset=True)
        if payload.asset_override is not None
        else None
    )
    lineage = (
        payload.lineage.model_dump(exclude_unset=True)
        if payload.lineage is not None
        else None
    )
    try:
        run, created, launch_meta = template_service.launch_template(
            session,
            template_id,
            idempotency_key=key,
            created_by=context.label,
            name=(payload.name or "").strip() or None,
            asset_override=asset_override,
            lineage=lineage,
            service_token=context.service_token,
        )
    except (
        template_service.TemplateError,
        token_service.ServiceTokenError,
    ) as error:
        raise _http_error(error) from error
    body = {
        "run_id": run.id,
        "template_id": template_id,
        "status": run.status,
        "status_url": f"/api/v1/eval-runs/{run.id}/status",
        "result_url": f"/api/v1/eval-runs/{run.id}/results",
        "created": created,
    }
    # Console compatibility: the one-click launch never received an override,
    # so its 202 body stays byte-for-byte identical to the pre-#429 contract.
    # An empty override object carries no intent and is normalized to absent by
    # the service, so keying off the resolved identity (not the request field)
    # also keeps a mismatched replay from emitting ``"asset": null``.
    if launch_meta.get("asset") is not None:
        body["asset"] = launch_meta["asset"]
    warnings = launch_meta.get("warnings") or []
    if warnings:
        body["warnings"] = warnings
    return body

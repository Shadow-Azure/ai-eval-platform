from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clients import get_provider_client
from ..db import get_session
from ..model_gateway.service import (
    GatewayError,
    credential_payload,
    model_config_payload,
    normalize_upstream_error,
    read_secret,
    usage_payload,
    usage_totals,
)
from ..models import Credential, ModelConfig, ModelUsage, utcnow
from ..secrets import SecretBoxError, get_secret_box, mask_secret
from ..seed import ensure_default_scope
from .dependencies import require_admin

router = APIRouter(tags=["models"])

API_STYLES = ("openai", "anthropic")


def _scope(session: Session) -> tuple[str, str]:
    tenant, _, project = ensure_default_scope(session)
    return tenant.id, project.id


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    fields: dict[str, str] | None = None,
) -> HTTPException:
    error: dict[str, object] = {"code": code, "message": message, "request_id": None}
    if fields:
        # The console maps ``fields`` onto form controls, so an invalid key,
        # endpoint or credential highlights the field that must change.
        error["fields"] = fields
    return HTTPException(status_code=status_code, detail={"error": error})


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    # SQLite drops tzinfo even though the value was written as UTC. Normalize
    # so ``last_used_at`` serializes with an offset and relative times are
    # correct in the console regardless of database dialect.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _usage_by_config(
    session: Session, tenant_id: str, config_ids: list[str]
) -> dict[str, dict[str, object]]:
    """Aggregate model usage once per list request (M5-02 recent-use column)."""

    if not config_ids:
        return {}
    rows = session.execute(
        select(
            ModelUsage.model_config_id.label("config_id"),
            func.max(ModelUsage.created_at).label("last_used_at"),
            func.count(ModelUsage.id).label("requests"),
            func.coalesce(func.sum(ModelUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(ModelUsage.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(ModelUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(ModelUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(
            ModelUsage.tenant_id == tenant_id,
            ModelUsage.model_config_id.in_(config_ids),
        )
        .group_by(ModelUsage.model_config_id)
    ).all()
    return {
        str(row.config_id): {
            "last_used_at": _as_datetime(row.last_used_at),
            "requests": int(row.requests or 0),
            "prompt_tokens": int(row.prompt_tokens or 0),
            "completion_tokens": int(row.completion_tokens or 0),
            "total_tokens": int(row.total_tokens or 0),
            "cost_usd": str(
                Decimal(str(row.cost_usd or 0)).quantize(Decimal("0.000001"))
            ),
        }
        for row in rows
    }


def _config_payload(
    session: Session,
    config: ModelConfig,
    *,
    credentials: dict[str, Credential] | None = None,
    usage: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Console payload: config fields plus its masked credential and usage.

    ``credential_payload`` only ever exposes ``secret_mask``; the ciphertext
    and key version never leave the gateway service.
    """

    payload = model_config_payload(config)
    credential = None
    if config.credential_id:
        if credentials is not None:
            credential = credentials.get(config.credential_id)
        else:
            credential = session.scalar(
                select(Credential).where(
                    Credential.id == config.credential_id,
                    Credential.tenant_id == config.tenant_id,
                )
            )
    payload["credential"] = (
        credential_payload(credential) if credential is not None else None
    )
    summary = (usage or {}).get(config.id) or {
        "last_used_at": None,
        "requests": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": "0.000000",
    }
    payload["last_used_at"] = summary["last_used_at"]
    payload["usage"] = {
        "requests": summary["requests"],
        "prompt_tokens": summary["prompt_tokens"],
        "completion_tokens": summary["completion_tokens"],
        "total_tokens": summary["total_tokens"],
        "cost_usd": summary["cost_usd"],
    }
    return payload


def _connectivity_fields(code: str, message: str) -> dict[str, str] | None:
    """Translate a gateway error code into the form field that caused it."""

    if code in (
        "authentication_failed",
        "credential_revoked",
        "credential_missing",
        "credential_unreadable",
    ):
        return {"secret": message}
    if code in ("provider_unavailable", "invalid_request", "upstream_error"):
        return {"base_url": message}
    return None


class CredentialCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    secret: str = Field(min_length=1, max_length=4096)
    kind: Literal["model", "resource"] = "model"


class CredentialRotate(BaseModel):
    secret: str = Field(min_length=1, max_length=4096)


class ModelConfigCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    model_name: str = Field(min_length=1, max_length=200)
    credential_id: str | None = None
    secret: str | None = Field(default=None, max_length=4096)
    api_style: str = Field(default="openai", max_length=32)
    limits: dict = Field(default_factory=dict)
    pricing: dict = Field(default_factory=dict)
    is_temporary: bool = False
    owner_eval_run_id: str | None = None


class ModelConfigPatch(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    api_style: str | None = Field(default=None, max_length=32)
    credential_id: str | None = None
    limits: dict | None = None
    pricing: dict | None = None


def _credential_or_404(session: Session, tenant_id: str, credential_id: str) -> Credential:
    credential = session.scalar(
        select(Credential).where(
            Credential.id == credential_id, Credential.tenant_id == tenant_id
        )
    )
    if credential is None:
        raise _error(
            404,
            "credential_not_found",
            "凭据不存在",
            fields={"credential_id": "凭据不存在或不属于当前项目"},
        )
    return credential


def _create_credential(
    session: Session,
    tenant_id: str,
    project_id: str,
    *,
    provider: str,
    secret: str,
    kind: str = "model",
) -> Credential:
    try:
        ciphertext, key_version = get_secret_box().encrypt(secret)
    except SecretBoxError as error:
        raise _error(
            503,
            "credential_encryption_unavailable",
            str(error),
            fields={"secret": "凭据加密不可用，暂时无法保存 Key"},
        ) from error
    credential = Credential(
        tenant_id=tenant_id,
        project_id=project_id,
        kind=kind,
        provider=provider,
        ciphertext=ciphertext,
        key_version=key_version,
        secret_mask=mask_secret(secret),
        last_rotated_at=utcnow(),
    )
    session.add(credential)
    session.flush()
    return credential


def _validate_base_url(base_url: str) -> str:
    if not base_url.startswith(("http://", "https://")):
        raise _error(
            422,
            "invalid_base_url",
            "base_url 必须是 http(s) 地址",
            fields={"base_url": "必须是 http:// 或 https:// 开头的地址"},
        )
    return base_url.rstrip("/")


def _validate_api_style(api_style: str) -> str:
    if api_style not in API_STYLES:
        raise _error(
            422,
            "invalid_api_style",
            f"api_style 必须是 {', '.join(API_STYLES)}",
            fields={"api_style": f"只支持 {', '.join(API_STYLES)}"},
        )
    return api_style


@router.post("/api/v1/credentials", status_code=201, dependencies=[Depends(require_admin)])
def create_credential(
    body: CredentialCreate, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, project_id = _scope(session)
    credential = _create_credential(
        session,
        tenant_id,
        project_id,
        provider=body.provider,
        secret=body.secret,
        kind=body.kind,
    )
    session.commit()
    return credential_payload(credential)


@router.get("/api/v1/credentials")
def list_credentials(
    kind: Annotated[Literal["model", "resource"] | None, Query()] = None,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    tenant_id, project_id = _scope(session)
    statement = select(Credential).where(
        Credential.tenant_id == tenant_id, Credential.project_id == project_id
    )
    if kind is not None:
        statement = statement.where(Credential.kind == kind)
    credentials = list(session.scalars(statement.order_by(Credential.created_at.desc())))
    return {
        "items": [credential_payload(item) for item in credentials],
        "page_info": {"next_cursor": None, "has_more": False},
    }


@router.post("/api/v1/credentials/{credential_id}/rotate", dependencies=[Depends(require_admin)])
def rotate_credential(
    credential_id: str,
    body: CredentialRotate,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    credential = _credential_or_404(session, tenant_id, credential_id)
    try:
        ciphertext, key_version = get_secret_box().encrypt(body.secret)
    except SecretBoxError as error:
        raise _error(503, "credential_encryption_unavailable", str(error)) from error
    credential.ciphertext = ciphertext
    credential.key_version = key_version
    credential.secret_mask = mask_secret(body.secret)
    credential.last_rotated_at = utcnow()
    credential.revoked_at = None
    session.commit()
    return credential_payload(credential)


@router.delete("/api/v1/credentials/{credential_id}", dependencies=[Depends(require_admin)])
def revoke_credential(
    credential_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    credential = _credential_or_404(session, tenant_id, credential_id)
    if credential.revoked_at is None:
        credential.revoked_at = utcnow()
        session.commit()
    return credential_payload(credential)


@router.post("/api/v1/model-configs", status_code=201, dependencies=[Depends(require_admin)])
def create_model_config(
    body: ModelConfigCreate, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, project_id = _scope(session)
    _validate_base_url(body.base_url)
    _validate_api_style(body.api_style)
    if bool(body.credential_id) == bool(body.secret):
        raise _error(
            422,
            "credential_required",
            "credential_id 与 secret 必须且只能提供一个",
            fields={
                "credential_id": "选择一个已有凭据，或填写新的 API Key",
                "secret": "填写新的 API Key，或选择已有凭据",
            },
        )
    credential_id = body.credential_id
    if body.secret is not None:
        credential = _create_credential(
            session,
            tenant_id,
            project_id,
            provider=body.provider,
            secret=body.secret,
        )
        credential_id = credential.id
    elif credential_id is not None:
        credential = _credential_or_404(session, tenant_id, credential_id)
        if credential.revoked_at is not None:
            raise _error(
                422,
                "credential_revoked",
                "凭据已撤销",
                fields={"credential_id": "该凭据已撤销，请轮换 Key 或选择其他凭据"},
            )
    config = ModelConfig(
        tenant_id=tenant_id,
        project_id=project_id,
        credential_id=credential_id,
        provider=body.provider,
        display_name=body.display_name,
        base_url=_validate_base_url(body.base_url),
        model_name=body.model_name,
        api_style=body.api_style,
        limits_json=body.limits,
        pricing_json=body.pricing,
        is_temporary=body.is_temporary,
        owner_eval_run_id=body.owner_eval_run_id,
    )
    session.add(config)
    session.commit()
    return _config_payload(session, config)


@router.get("/api/v1/model-configs")
def list_model_configs(
    include_temporary: Annotated[bool, Query()] = False,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    tenant_id, project_id = _scope(session)
    statement = select(ModelConfig).where(
        ModelConfig.tenant_id == tenant_id, ModelConfig.project_id == project_id
    )
    if not include_temporary:
        # Design 02 §3.3 / 05 §5: a temporary model config belongs to one eval
        # run and never enters the shared catalog the console renders.
        statement = statement.where(ModelConfig.is_temporary.is_(False))
    configs = list(session.scalars(statement.order_by(ModelConfig.created_at.desc())))
    config_ids = [item.id for item in configs]
    credential_ids = [item.credential_id for item in configs if item.credential_id]
    credentials = (
        {
            item.id: item
            for item in session.scalars(
                select(Credential).where(
                    Credential.tenant_id == tenant_id, Credential.id.in_(credential_ids)
                )
            )
        }
        if credential_ids
        else {}
    )
    usage = _usage_by_config(session, tenant_id, config_ids)
    return {
        "items": [
            _config_payload(session, item, credentials=credentials, usage=usage)
            for item in configs
        ],
        "page_info": {"next_cursor": None, "has_more": False},
    }


def _config_or_404(session: Session, tenant_id: str, config_id: str) -> ModelConfig:
    config = session.scalar(
        select(ModelConfig).where(
            ModelConfig.id == config_id, ModelConfig.tenant_id == tenant_id
        )
    )
    if config is None:
        raise _error(404, "model_config_not_found", "模型配置不存在")
    return config


@router.get("/api/v1/model-configs/{config_id}")
def get_model_config(
    config_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    config = _config_or_404(session, tenant_id, config_id)
    return _config_payload(session, config)


@router.patch("/api/v1/model-configs/{config_id}", dependencies=[Depends(require_admin)])
def patch_model_config(
    config_id: str,
    body: ModelConfigPatch,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    config = _config_or_404(session, tenant_id, config_id)
    updates = body.model_dump(exclude_none=True)
    if "credential_id" in updates:
        credential = _credential_or_404(session, tenant_id, updates["credential_id"])
        if credential.revoked_at is not None:
            raise _error(
                422,
                "credential_revoked",
                "凭据已撤销",
                fields={"credential_id": "该凭据已撤销，请轮换 Key 或选择其他凭据"},
            )
    if "base_url" in updates:
        updates["base_url"] = _validate_base_url(updates["base_url"])
    if "api_style" in updates:
        updates["api_style"] = _validate_api_style(updates["api_style"])
    for field, value in updates.items():
        if field == "limits":
            config.limits_json = value
        elif field == "pricing":
            config.pricing_json = value
        else:
            setattr(config, field, value)
    session.commit()
    return _config_payload(session, config)


@router.post("/api/v1/model-configs/{config_id}/test", dependencies=[Depends(require_admin)])
async def test_model_config(
    config_id: str,
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_provider_client),
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    config = _config_or_404(session, tenant_id, config_id)
    try:
        secret = read_secret(session, config, get_secret_box())
    except GatewayError as error:
        raise _error(
            error.status_code,
            error.code,
            error.message,
            fields=_connectivity_fields(error.code, error.message),
        ) from error
    if config.api_style == "anthropic":
        url = f"{config.base_url.rstrip('/')}/v1/models"
        headers = {"x-api-key": secret, "anthropic-version": "2023-06-01"}
    else:
        url = f"{config.base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {secret}"}
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as error:
        message = f"模型端点不可达：{error}"
        raise _error(
            503,
            "provider_unavailable",
            message,
            fields=_connectivity_fields("provider_unavailable", message),
        ) from error
    if response.status_code >= 400:
        mapped = normalize_upstream_error(response.status_code)
        raise _error(
            mapped.status_code,
            mapped.code,
            mapped.message,
            fields=_connectivity_fields(mapped.code, mapped.message),
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    models = payload.get("data") if isinstance(payload, dict) else None
    count = len(models) if isinstance(models, list) else None
    return {
        "status": "ok",
        "status_code": response.status_code,
        "model_count": count,
    }


@router.get("/api/v1/model-usage")
def list_model_usage(
    eval_run_id: Annotated[str | None, Query(max_length=36)] = None,
    shard_id: Annotated[str | None, Query(max_length=64)] = None,
    model_config_id: Annotated[str | None, Query(max_length=36)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """Per-request model usage, filterable by eval run and shard (M3-02)."""

    tenant_id, _ = _scope(session)
    statement = select(ModelUsage).where(ModelUsage.tenant_id == tenant_id)
    if eval_run_id:
        statement = statement.where(ModelUsage.eval_run_id == eval_run_id)
    if shard_id:
        statement = statement.where(ModelUsage.shard_id == shard_id)
    if model_config_id:
        statement = statement.where(ModelUsage.model_config_id == model_config_id)
    rows = list(
        session.scalars(statement.order_by(ModelUsage.created_at.desc()).limit(limit))
    )
    return {
        "items": [usage_payload(row) for row in rows],
        "totals": usage_totals(rows),
        "page_info": {"next_cursor": None, "has_more": False},
    }

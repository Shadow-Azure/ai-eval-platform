from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients import get_provider_client
from ..db import get_session
from ..model_gateway.service import (
    GatewayError,
    credential_payload,
    model_config_payload,
    normalize_upstream_error,
    read_secret,
)
from ..models import Credential, ModelConfig, utcnow
from ..secrets import SecretBoxError, get_secret_box, mask_secret
from ..seed import ensure_default_scope

router = APIRouter(tags=["models"])


def _scope(session: Session) -> tuple[str, str]:
    tenant, _, project = ensure_default_scope(session)
    return tenant.id, project.id


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "request_id": None}},
    )


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
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
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
        raise _error(404, "credential_not_found", "凭据不存在")
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
        raise _error(503, "credential_encryption_unavailable", str(error)) from error
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
        raise _error(422, "invalid_base_url", "base_url 必须是 http(s) 地址")
    return base_url.rstrip("/")


@router.post("/api/v1/credentials", status_code=201)
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
def list_credentials(session: Session = Depends(get_session)) -> dict[str, object]:
    tenant_id, project_id = _scope(session)
    credentials = list(
        session.scalars(
            select(Credential)
            .where(Credential.tenant_id == tenant_id, Credential.project_id == project_id)
            .order_by(Credential.created_at.desc())
        )
    )
    return {
        "items": [credential_payload(item) for item in credentials],
        "page_info": {"next_cursor": None, "has_more": False},
    }


@router.post("/api/v1/credentials/{credential_id}/rotate")
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


@router.delete("/api/v1/credentials/{credential_id}")
def revoke_credential(
    credential_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    credential = _credential_or_404(session, tenant_id, credential_id)
    if credential.revoked_at is None:
        credential.revoked_at = utcnow()
        session.commit()
    return credential_payload(credential)


@router.post("/api/v1/model-configs", status_code=201)
def create_model_config(
    body: ModelConfigCreate, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, project_id = _scope(session)
    _validate_base_url(body.base_url)
    if bool(body.credential_id) == bool(body.secret):
        raise _error(
            422,
            "credential_required",
            "credential_id 与 secret 必须且只能提供一个",
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
            raise _error(422, "credential_revoked", "凭据已撤销")
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
    return model_config_payload(config)


@router.get("/api/v1/model-configs")
def list_model_configs(session: Session = Depends(get_session)) -> dict[str, object]:
    tenant_id, project_id = _scope(session)
    configs = list(
        session.scalars(
            select(ModelConfig)
            .where(ModelConfig.tenant_id == tenant_id, ModelConfig.project_id == project_id)
            .order_by(ModelConfig.created_at.desc())
        )
    )
    return {
        "items": [model_config_payload(item) for item in configs],
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
    return model_config_payload(_config_or_404(session, tenant_id, config_id))


@router.patch("/api/v1/model-configs/{config_id}")
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
            raise _error(422, "credential_revoked", "凭据已撤销")
    if "base_url" in updates:
        updates["base_url"] = _validate_base_url(updates["base_url"])
    for field, value in updates.items():
        if field == "limits":
            config.limits_json = value
        elif field == "pricing":
            config.pricing_json = value
        else:
            setattr(config, field, value)
    session.commit()
    return model_config_payload(config)


@router.post("/api/v1/model-configs/{config_id}/test")
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
        raise _error(error.status_code, error.code, error.message) from error
    url = f"{config.base_url.rstrip('/')}/models"
    try:
        response = await client.get(url, headers={"Authorization": f"Bearer {secret}"})
    except httpx.HTTPError as error:
        raise _error(503, "provider_unavailable", f"模型端点不可达：{error}") from error
    if response.status_code >= 400:
        mapped = normalize_upstream_error(response.status_code)
        raise _error(mapped.status_code, mapped.code, mapped.message)
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

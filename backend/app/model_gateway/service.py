"""Model gateway application service.

The gateway owns provider credentials, budget accounting and error
normalization. It records usage per attempt so the platform, not the runner,
is the authority on model spend.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Credential, ModelConfig, ModelUsage
from ..secrets import SecretBoxError, mask_secret
from .tokens import GatewayClaims


class GatewayError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def extract_bearer(header: str | None) -> str:
    if not header or not header.startswith("Bearer "):
        raise GatewayError(401, "missing_token", "缺少模型网关短期 token")
    token = header.removeprefix("Bearer ").strip()
    if not token:
        raise GatewayError(401, "missing_token", "缺少模型网关短期 token")
    return token


def resolve_config(session: Session, claims: GatewayClaims) -> ModelConfig:
    config = session.scalar(
        select(ModelConfig).where(
            ModelConfig.id == claims.model_config_id,
            ModelConfig.tenant_id == claims.tenant_id,
        )
    )
    if config is None:
        raise GatewayError(403, "model_config_forbidden", "模型配置不存在或不可用")
    return config


def read_secret(session: Session, config: ModelConfig, secret_box) -> str:
    if not config.credential_id:
        raise GatewayError(403, "credential_missing", "模型配置没有绑定凭据")
    credential = session.scalar(
        select(Credential).where(
            Credential.id == config.credential_id,
            Credential.tenant_id == config.tenant_id,
        )
    )
    if credential is None or credential.revoked_at is not None:
        raise GatewayError(403, "credential_revoked", "模型凭据不存在或已撤销")
    try:
        return secret_box.decrypt(credential.ciphertext, credential.key_version)
    except SecretBoxError as error:
        raise GatewayError(503, "credential_unreadable", str(error)) from error


def check_model_allowed(model: str, claims: GatewayClaims, config: ModelConfig) -> None:
    allowed = claims.allowed_models or (config.model_name,)
    if model not in allowed:
        raise GatewayError(403, "model_forbidden", "token 无权调用该模型")


def spent_usd(session: Session, claims: GatewayClaims) -> Decimal:
    statement = select(func.coalesce(func.sum(ModelUsage.cost_usd), 0))
    if claims.case_run_id:
        statement = statement.where(ModelUsage.case_run_id == claims.case_run_id)
    elif claims.eval_run_id:
        statement = statement.where(ModelUsage.eval_run_id == claims.eval_run_id)
    else:
        return Decimal("0")
    value = session.scalar(statement)
    return Decimal(str(value or 0))


def ensure_budget(session: Session, claims: GatewayClaims) -> None:
    if claims.budget_usd is None:
        return
    if spent_usd(session, claims) >= Decimal(str(claims.budget_usd)):
        raise GatewayError(402, "budget_paused", "模型预算已用尽，评测暂停")


def usage_from_payload(payload: dict) -> tuple[int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    prompt = int(prompt) if isinstance(prompt, (int, float)) else 0
    completion = int(completion) if isinstance(completion, (int, float)) else 0
    return prompt, completion


def cost_for(config: ModelConfig, prompt_tokens: int, completion_tokens: int) -> Decimal:
    pricing = config.pricing_json if isinstance(config.pricing_json, dict) else {}
    input_rate = Decimal(str(pricing.get("input_per_1m", 0) or 0))
    output_rate = Decimal(str(pricing.get("output_per_1m", 0) or 0))
    cost = (
        Decimal(prompt_tokens) / Decimal(1_000_000) * input_rate
        + Decimal(completion_tokens) / Decimal(1_000_000) * output_rate
    )
    return cost.quantize(Decimal("0.000001"))


def record_usage(
    session: Session,
    claims: GatewayClaims,
    config: ModelConfig,
    *,
    status: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    request_id: str | None = None,
) -> ModelUsage:
    usage = ModelUsage(
        tenant_id=claims.tenant_id,
        model_config_id=config.id,
        eval_run_id=claims.eval_run_id,
        case_run_id=claims.case_run_id,
        attempt=claims.attempt,
        request_id=request_id,
        status=status,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        cost_usd=cost_for(config, prompt_tokens, completion_tokens),
    )
    session.add(usage)
    session.commit()
    return usage


def upstream_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def normalize_upstream_error(status_code: int) -> GatewayError:
    if status_code in (401, 403):
        return GatewayError(502, "authentication_failed", "模型供应商鉴权失败")
    if status_code == 429:
        return GatewayError(429, "rate_limited", "模型供应商限流，请重试")
    if 400 <= status_code < 500:
        return GatewayError(400, "invalid_request", "模型请求无效")
    if status_code >= 500:
        return GatewayError(503, "provider_unavailable", "模型供应商暂不可用")
    return GatewayError(502, "upstream_error", "模型供应商返回异常")


def provider_request_id(response: httpx.Response) -> str | None:
    return response.headers.get("x-request-id") or response.headers.get("request-id")


def credential_payload(credential: Credential) -> dict[str, object]:
    return {
        "id": credential.id,
        "kind": credential.kind,
        "provider": credential.provider,
        "secret_mask": credential.secret_mask,
        "expires_at": credential.expires_at,
        "last_rotated_at": credential.last_rotated_at,
        "revoked_at": credential.revoked_at,
        "created_at": credential.created_at,
    }


def model_config_payload(config: ModelConfig) -> dict[str, object]:
    return {
        "id": config.id,
        "provider": config.provider,
        "display_name": config.display_name,
        "base_url": config.base_url,
        "model_name": config.model_name,
        "api_style": config.api_style,
        "credential_id": config.credential_id,
        "limits": config.limits_json,
        "pricing": config.pricing_json,
        "is_temporary": config.is_temporary,
        "owner_eval_run_id": config.owner_eval_run_id,
        "created_at": config.created_at,
    }


__all__ = [
    "GatewayError",
    "check_model_allowed",
    "cost_for",
    "credential_payload",
    "ensure_budget",
    "extract_bearer",
    "mask_secret",
    "model_config_payload",
    "normalize_upstream_error",
    "provider_request_id",
    "read_secret",
    "record_usage",
    "resolve_config",
    "spent_usd",
    "upstream_url",
    "usage_from_payload",
]

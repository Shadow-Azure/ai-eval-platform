"""Short-lived model gateway tokens (M3-02).

The wire format is intentionally the M2-05 ``services.short_token`` format
(``v1.<base64url(JSON claims)>.<base64url(HMAC-SHA256)>``) so one verifier
pattern covers both case callbacks and model calls. A ``kind`` marker keeps the
two token families apart: a task token must never be accepted by the model
gateway and a model token must never be accepted by a case callback.

Runners receive a token bound to (tenant, model configuration, eval run,
shard, attempt) and exchange it for the provider key inside the platform
gateway. The provider key itself never enters the runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..services.short_token import (
    ShortTokenInvalid,
    issue_short_token,
    verify_short_token,
)

TOKEN_KIND = "model-gateway"


class GatewayTokenError(ValueError):
    """Token classification consumed by the gateway API."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class GatewayClaims:
    tenant_id: str
    model_config_id: str
    allowed_models: tuple[str, ...] = field(default_factory=tuple)
    eval_run_id: str | None = None
    shard_id: str | None = None
    attempt: int | None = None
    budget_cny: float | None = None


def issue_gateway_token(
    claims: GatewayClaims,
    secret: str,
    *,
    ttl_seconds: int = 900,
    now: datetime | None = None,
) -> str:
    """Sign a model-gateway token in the shared M2-05 wire format."""

    payload: dict[str, Any] = {
        "kind": TOKEN_KIND,
        "tenant_id": claims.tenant_id,
        "model_config_id": claims.model_config_id,
        "allowed_models": list(claims.allowed_models),
    }
    if claims.eval_run_id is not None:
        payload["eval_run_id"] = claims.eval_run_id
    if claims.shard_id is not None:
        payload["shard_id"] = claims.shard_id
    if claims.attempt is not None:
        payload["attempt"] = claims.attempt
    if claims.budget_cny is not None:
        payload["budget_cny"] = claims.budget_cny
    return issue_short_token(payload, secret, ttl_seconds=ttl_seconds, now=now)


def verify_gateway_token(
    token: str,
    secret: str,
    *,
    now: datetime | None = None,
) -> GatewayClaims:
    """Verify signature/expiry and parse a model-gateway token."""

    try:
        payload = verify_short_token(token, secret, now=now)
    except ShortTokenInvalid as error:
        code = "token_expired" if "过期" in error.reason else "invalid_token"
        raise GatewayTokenError(code, error.reason) from error

    if payload.get("kind") != TOKEN_KIND:
        raise GatewayTokenError("token_kind_mismatch", "token 不是模型网关 token")
    tenant_id = payload.get("tenant_id")
    model_config_id = payload.get("model_config_id")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise GatewayTokenError("invalid_token", "token 缺少 tenant_id")
    if not isinstance(model_config_id, str) or not model_config_id:
        raise GatewayTokenError("invalid_token", "token 缺少 model_config_id")

    models = payload.get("allowed_models") or []
    if not isinstance(models, list) or any(not isinstance(item, str) for item in models):
        raise GatewayTokenError("invalid_token", "allowed_models 无效")

    eval_run_id = payload.get("eval_run_id")
    shard_id = payload.get("shard_id")
    attempt = payload.get("attempt")
    budget = payload.get("budget_cny")
    if eval_run_id is not None and not isinstance(eval_run_id, str):
        raise GatewayTokenError("invalid_token", "eval_run_id 无效")
    if shard_id is not None and not isinstance(shard_id, str):
        raise GatewayTokenError("invalid_token", "shard_id 无效")
    if attempt is not None and not isinstance(attempt, int):
        raise GatewayTokenError("invalid_token", "attempt 无效")
    if budget is not None and not isinstance(budget, (int, float)):
        raise GatewayTokenError("invalid_token", "budget_cny 无效")

    return GatewayClaims(
        tenant_id=tenant_id,
        model_config_id=model_config_id,
        allowed_models=tuple(models),
        eval_run_id=eval_run_id,
        shard_id=shard_id,
        attempt=attempt,
        budget_cny=float(budget) if budget is not None else None,
    )


__all__ = [
    "TOKEN_KIND",
    "GatewayClaims",
    "GatewayTokenError",
    "issue_gateway_token",
    "verify_gateway_token",
]

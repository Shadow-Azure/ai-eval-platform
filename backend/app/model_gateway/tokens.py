"""Short-lived model gateway tokens.

Runners never receive provider keys. A task token carries the tenant, model
configuration, allowed model names, attempt binding and optional budget; the
gateway verifies it before proxying to the provider.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


class TokenError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GatewayClaims:
    tenant_id: str
    model_config_id: str
    allowed_models: tuple[str, ...]
    exp: int
    eval_run_id: str | None = None
    case_run_id: str | None = None
    attempt: int | None = None
    budget_usd: float | None = None


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(claims: GatewayClaims, secret: str) -> str:
    if not secret:
        raise TokenError("token_secret_missing", "网关 token 密钥未配置")
    payload = {
        "tenant_id": claims.tenant_id,
        "model_config_id": claims.model_config_id,
        "allowed_models": list(claims.allowed_models),
        "exp": claims.exp,
        "eval_run_id": claims.eval_run_id,
        "case_run_id": claims.case_run_id,
        "attempt": claims.attempt,
        "budget_usd": claims.budget_usd,
    }
    encoded = _b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(
        secret.encode("utf-8"), f"v1.{encoded}".encode("ascii"), hashlib.sha256
    ).digest()
    return f"v1.{encoded}.{_b64encode(signature)}"


def verify_token(token: str, secret: str, *, now: int | None = None) -> GatewayClaims:
    if not secret:
        raise TokenError("token_secret_missing", "网关 token 密钥未配置")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise TokenError("invalid_token", "token 格式无效")
    encoded, signature = parts[1], parts[2]
    expected = hmac.new(
        secret.encode("utf-8"), f"v1.{encoded}".encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(_b64encode(expected), signature):
        raise TokenError("invalid_token", "token 签名无效")
    try:
        payload = json.loads(_b64decode(encoded))
    except (ValueError, json.JSONDecodeError) as error:
        raise TokenError("invalid_token", "token 载荷无效") from error
    if not isinstance(payload, dict):
        raise TokenError("invalid_token", "token 载荷无效")
    exp = payload.get("exp")
    if not isinstance(exp, int):
        raise TokenError("invalid_token", "token 缺少过期时间")
    current = int(time.time()) if now is None else now
    if exp <= current:
        raise TokenError("token_expired", "token 已过期")
    tenant_id = payload.get("tenant_id")
    model_config_id = payload.get("model_config_id")
    if not isinstance(tenant_id, str) or not isinstance(model_config_id, str):
        raise TokenError("invalid_token", "token 缺少租户或模型配置")
    models = payload.get("allowed_models") or []
    if not isinstance(models, list) or any(not isinstance(item, str) for item in models):
        raise TokenError("invalid_token", "allowed_models 无效")
    budget = payload.get("budget_usd")
    if budget is not None and not isinstance(budget, (int, float)):
        raise TokenError("invalid_token", "budget_usd 无效")
    return GatewayClaims(
        tenant_id=tenant_id,
        model_config_id=model_config_id,
        allowed_models=tuple(models),
        exp=exp,
        eval_run_id=payload.get("eval_run_id"),
        case_run_id=payload.get("case_run_id"),
        attempt=payload.get("attempt"),
        budget_usd=float(budget) if budget is not None else None,
    )

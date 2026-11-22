"""Model gateway application service.

The gateway owns provider credentials, budget accounting and error
normalization. It records usage per attempt so the platform, not the runner,
is the authority on model spend.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Credential,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    ModelConfig,
    ModelUsage,
)
from ..money import MICRO, Money, money_str
from ..secrets import SecretBoxError, mask_secret
from ..services.evaluations import TERMINAL_ATTEMPT_STATUSES
from ..services.trace_redaction import redact_text
from .tokens import GatewayClaims


class GatewayError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        upstream_message: str | None = None,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.upstream_message = upstream_message
        self.upstream_status = upstream_status


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


def _check_run_state(run: EvalRun) -> None:
    """Reject every token of a cancelled or budget-paused run.

    M4-02 A2 / M4-05: tokens are stateless and cannot be revoked one by one,
    so the run row is the global revocation point for cancellation and budget
    pause. This check intentionally stays run-wide (issue #358, requirement 3).
    """

    if run.status in ("cancelling", "cancelled"):
        raise GatewayError(409, "run_cancelled", "评测已取消，拒绝模型调用")
    if run.status in ("pausing_budget", "paused_budget"):
        raise GatewayError(409, "budget_paused", "模型预算已暂停，拒绝模型调用")


def _legacy_tokens_retired(
    deadline: datetime | None, now: datetime | None = None
) -> bool:
    """Whether the compatibility window for shard-level model tokens closed."""

    if deadline is None:
        return False
    current = now or datetime.now(UTC)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current >= deadline


def validate_claims_binding(
    session: Session,
    claims: GatewayClaims,
    *,
    legacy_token_deadline: datetime | None = None,
    now: datetime | None = None,
) -> None:
    """Reject tokens issued for another run/shard/case attempt.

    M8 (#358): a freshly issued token carries ``case_run_id`` and
    ``case_attempt_id``. The gateway resolves exactly that attempt row and
    revokes only when *its* status is terminal, so a terminal sibling case in
    the same shard cannot block a running case. Run-level cancellation and
    budget pause remain global.

    Tokens minted before the switch carry no case dimensions. During the
    compatibility window they use the legacy shard-wide rule: revoke only when
    every attempt of that shard/attempt is terminal. After
    ``legacy_token_deadline`` they fail closed. A partially specified case
    binding, or a fully unbound token, is never treated as legacy.
    """

    if claims.eval_run_id is None:
        raise GatewayError(403, "token_binding_mismatch", "模型 token 缺少评测绑定")
    if claims.shard_id is None or claims.attempt is None:
        raise GatewayError(403, "token_binding_mismatch", "token 缺少分片或 attempt 绑定")

    has_case_run = claims.case_run_id is not None
    has_case_attempt = claims.case_attempt_id is not None
    if has_case_run != has_case_attempt:
        raise GatewayError(403, "token_binding_mismatch", "token 的 case 维度不完整")

    if has_case_run:
        row = session.execute(
            select(EvalCaseAttempt, EvalRun)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .join(EvalRun, EvalRun.id == EvalCaseRun.eval_run_id)
            .where(
                EvalCaseAttempt.id == claims.case_attempt_id,
                EvalCaseAttempt.eval_case_run_id == claims.case_run_id,
                EvalCaseAttempt.tenant_id == claims.tenant_id,
                EvalCaseAttempt.shard_id == claims.shard_id,
                EvalCaseAttempt.attempt_no == claims.attempt,
                EvalCaseRun.tenant_id == claims.tenant_id,
                EvalCaseRun.id == claims.case_run_id,
                EvalCaseRun.eval_run_id == claims.eval_run_id,
            )
        ).one_or_none()
        if row is None:
            raise GatewayError(
                403, "token_binding_mismatch", "token 与评测/分片/case attempt 不一致"
            )
        attempt, run = row
        _check_run_state(run)
        if attempt.status in TERMINAL_ATTEMPT_STATUSES:
            # Only this case attempt is revoked. The retry creates a new
            # attempt row, so the old token cannot become valid again.
            raise GatewayError(409, "stale_attempt", "attempt 已结束，拒绝模型调用")
        return

    # Legacy shard-level token (compatibility window only).
    if _legacy_tokens_retired(legacy_token_deadline, now):
        raise GatewayError(
            409,
            "legacy_token_retired",
            "shard 级模型 token 已超过兼容期，拒绝模型调用",
        )
    rows = session.execute(
        select(EvalCaseAttempt, EvalRun)
        .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
        .join(EvalRun, EvalRun.id == EvalCaseRun.eval_run_id)
        .where(
            EvalCaseRun.tenant_id == claims.tenant_id,
            EvalCaseRun.eval_run_id == claims.eval_run_id,
            EvalCaseAttempt.shard_id == claims.shard_id,
            EvalCaseAttempt.attempt_no == claims.attempt,
        )
    ).all()
    if not rows:
        raise GatewayError(403, "token_binding_mismatch", "token 与评测/分片/attempt 不一致")
    # Every row of this shard/attempt shares the run, so one state check is
    # enough. Do not use .first(): the terminal check must see all cases.
    _check_run_state(rows[0][1])
    if all(attempt.status in TERMINAL_ATTEMPT_STATUSES for attempt, _ in rows):
        raise GatewayError(409, "stale_attempt", "attempt 已结束，拒绝模型调用")


def check_model_allowed(model: str, claims: GatewayClaims, config: ModelConfig) -> None:
    allowed = claims.allowed_models or (config.model_name,)
    if model not in allowed:
        raise GatewayError(403, "model_forbidden", "token 无权调用该模型")


def spent_cny(session: Session, claims: GatewayClaims) -> Money:
    if not claims.eval_run_id:
        return Money.zero()
    statement = select(func.coalesce(func.sum(ModelUsage.cost_cny), 0)).where(
        ModelUsage.eval_run_id == claims.eval_run_id
    )
    value = session.scalar(statement)
    return Money.of(value or 0)


def budget_limit_cny(session: Session, claims: GatewayClaims) -> Money | None:
    """Current authoritative budget for the token's run.

    The token carries the budget snapshot from manifest time, but a resume can
    raise it while an old token is still in use. Reading the run row keeps the
    enforcement (and the budget pause) authoritative without reissuing tokens.
    """

    if claims.eval_run_id:
        run = session.get(EvalRun, claims.eval_run_id)
        if run is not None and run.model_budget_cny is not None:
            return Money.of(run.model_budget_cny)
    if claims.budget_cny is None:
        return None
    return Money.of(claims.budget_cny)


def ensure_budget(session: Session, claims: GatewayClaims) -> None:
    limit = budget_limit_cny(session, claims)
    if limit is None:
        return
    if spent_cny(session, claims) >= limit:
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


def cost_for(config: ModelConfig, prompt_tokens: int, completion_tokens: int) -> Money:
    pricing = config.pricing_json if isinstance(config.pricing_json, dict) else {}
    input_rate = Money.of(pricing.get("input_per_1m", 0))
    output_rate = Money.of(pricing.get("output_per_1m", 0))
    cost = (
        input_rate * Decimal(prompt_tokens) / Decimal(1_000_000)
        + output_rate * Decimal(completion_tokens) / Decimal(1_000_000)
    )
    return cost.quantize(MICRO)


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
        shard_id=claims.shard_id,
        case_run_id=claims.case_run_id,
        attempt=claims.attempt,
        request_id=request_id,
        status=status,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        cost_cny=cost_for(config, prompt_tokens, completion_tokens).amount,
    )
    session.add(usage)
    session.commit()
    return usage


def upstream_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def upstream_messages_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1/messages"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/messages"
    return f"{normalized}/v1/messages"


def anthropic_usage_from_payload(payload: dict) -> tuple[int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("message"), dict):
        nested = payload["message"].get("usage")
        if isinstance(nested, dict):
            usage = nested
    if not isinstance(usage, dict):
        return 0, 0
    prompt = usage.get("input_tokens")
    completion = usage.get("output_tokens")
    return (
        int(prompt) if isinstance(prompt, (int, float)) else 0,
        int(completion) if isinstance(completion, (int, float)) else 0,
    )


def usage_payload(usage: ModelUsage) -> dict[str, object]:
    return {
        "id": usage.id,
        "model_config_id": usage.model_config_id,
        "eval_run_id": usage.eval_run_id,
        "shard_id": usage.shard_id,
        "attempt": usage.attempt,
        "status": usage.status,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cost_cny": money_str(usage.cost_cny),
        "request_id": usage.request_id,
        "created_at": usage.created_at,
    }


def usage_totals(usages: list[ModelUsage]) -> dict[str, object]:
    prompt = sum(item.prompt_tokens for item in usages)
    completion = sum(item.completion_tokens for item in usages)
    total = sum(item.total_tokens for item in usages)
    cost = Money.sum(item.cost_cny for item in usages).quantize(MICRO)
    return {
        "requests": len(usages),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cost_cny": cost.as_str(),
    }


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


def upstream_error_message(body: bytes | str | None, *, limit: int = 500) -> str | None:
    """Extract the provider's own error text from an upstream error body.

    M8 (#375): the console must show "Token Plan 用量上限 (2056)" instead of a
    generic "rate limited". Providers answer with either a JSON error envelope
    or a short plain-text body; both are redacted and bounded before they are
    echoed to the runner (never raw headers or credentials).
    """

    if body is None:
        return None
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - decode with replace cannot raise
            return None
    else:
        text = body
    text = text.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if isinstance(message, str) and message.strip():
                text = message
                if isinstance(code, str) and code.strip() and code not in message:
                    text = f"{code}: {message}"
            elif isinstance(error.get("type"), str):
                text = str(error["type"])
        elif isinstance(payload.get("message"), str):
            text = str(payload["message"])
    return redact_text(text, limit=limit) or None


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
    "anthropic_usage_from_payload",
    "model_config_payload",
    "normalize_upstream_error",
    "provider_request_id",
    "read_secret",
    "record_usage",
    "resolve_config",
    "spent_cny",
    "upstream_messages_url",
    "upstream_url",
    "usage_from_payload",
    "usage_payload",
    "usage_totals",
    "validate_claims_binding",
]

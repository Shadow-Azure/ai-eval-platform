"""Platform-issued short-term case tokens (M2-05, design 06 §4).

Wire format is shared with the runner reference implementation
`runner/lib/auth.mjs`: `v1.<base64url(JSON claims)>.<base64url(HMAC-SHA256)>`.
The platform signs tokens bound to (eval_run_id, shard_id, attempt, pool_id);
case callbacks verify signature, expiry and the binding against the stored
attempt before touching state. No asymmetric crypto and no third-party
dependency: the dispatch/execution threat model for M2 is the fixed runner
host, and the secret rides the platform env, never the GitHub payload.
"""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest, new
from typing import Any


class ShortTokenInvalid(Exception):
    """Token is malformed, tampered with, expired or signed by another key."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _b64url(data: bytes) -> str:
    return urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(value + padding)


def issue_short_token(
    claims: dict[str, Any],
    secret: str,
    *,
    ttl_seconds: int = 600,
    now: datetime | None = None,
) -> str:
    """Sign claims into a runner-compatible short-lived token."""

    if not secret:
        raise ShortTokenInvalid("token secret 不能为空")
    now = now or datetime.now(UTC)
    payload = {
        **claims,
        "exp": int(now.timestamp()) + ttl_seconds,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64url(new(secret.encode(), body.encode(), sha256).digest())
    return f"v1.{body}.{signature}"


def verify_short_token(
    token: str,
    secret: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify signature + expiry and return the claims."""

    if not secret:
        raise ShortTokenInvalid("token secret 不能为空")
    if not isinstance(token, str):
        raise ShortTokenInvalid("缺少 token")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise ShortTokenInvalid("token 格式非法")

    body, signature = parts[1], parts[2]
    expected = _b64url(new(secret.encode(), body.encode(), sha256).digest())
    actual = _b64url_decode(signature)
    if not compare_digest(actual, _b64url_decode(expected)):
        raise ShortTokenInvalid("token 签名校验失败")

    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ShortTokenInvalid("token payload 不是 JSON") from exc
    if not isinstance(payload, dict):
        raise ShortTokenInvalid("token payload 不是 JSON 对象")

    exp = payload.get("exp")
    now = now or datetime.now(UTC)
    if not isinstance(exp, (int, float)) or exp < now.timestamp():
        raise ShortTokenInvalid("token 已过期")
    return payload

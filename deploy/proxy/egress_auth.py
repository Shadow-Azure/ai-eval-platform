#!/usr/bin/env python3
"""Squid basic-auth helper for the M8-14 public GitHub egress listener.

Squid speaks a simple line protocol on stdin/stdout:

    stdin : "<username> <password>\\n" for every credential check
    stdout: "OK\\n" or "ERR\\n" for that line, flushed immediately

The platform puts a short-lived signed egress token in the Basic *username*
slot and leaves the password empty, so the password half of the line is
ignored. Tokens use the v1 short-token format shared with
``backend/app/services/short_token.py`` and ``runner/lib/auth.mjs``:

    v1.<base64url(JSON claims)>.<base64url(HMAC-SHA256(secret, body))>

This file must stay byte-compatible with ``short_token.py``: same HMAC over
the base64url body (no re-serialisation), same unpadded base64url alphabet,
and the same claim semantics. Verification here checks the signature,
``exp`` and ``scope == "egress"``; the run/shard/attempt bindings are issued
for audit but are not checked at the proxy (it has no database).

Configuration comes from ``RUNNER_TASK_SECRET`` (the API's task-token secret,
injected from GitHub Secrets). An empty secret fails closed: every credential
is rejected. Nothing secret is ever printed; rejections are silent and show up
in Squid's access log as 407/denied.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import sys
import time
from typing import Any

TOKEN_VERSION = "v1"
EGRESS_SCOPE = "egress"
MAX_LINE_BYTES = 8192


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _verify(token: str, secret: str, *, now: float | None = None) -> bool:
    """Return True only for a well-formed, unexpired, egress-scoped token."""

    if not secret or not token or len(token) > MAX_LINE_BYTES:
        return False
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_VERSION:
        return False
    body, signature = parts[1], parts[2]
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    try:
        actual = _b64url_decode(signature)
    except (binascii.Error, ValueError):
        return False
    if not hmac.compare_digest(actual, expected):
        return False

    try:
        claims: Any = json.loads(_b64url_decode(body))
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(claims, dict):
        return False

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    if exp < (time.time() if now is None else now):
        return False
    return claims.get("scope") == EGRESS_SCOPE


def main() -> int:
    secret = os.environ.get("RUNNER_TASK_SECRET", "")
    out = sys.stdout
    for raw in sys.stdin:
        line = raw.rstrip("\r\n")
        if not line:
            continue
        # Squid sends "username password"; the token is the username half.
        user, _, _password = line.partition(" ")
        ok = _verify(user.strip(), secret)
        out.write("OK\n" if ok else "ERR\n")
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

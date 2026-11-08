"""Platform-issued short-term case tokens (M2-05, design 06 §4).

Wire format must stay compatible with `runner/lib/auth.mjs`:
`v1.<base64url(JSON claims)>.<base64url(HMAC-SHA256(body))>`. The runner side
verifies signature + bindings; the platform side issues and verifies the same
shape, so the two languages stay pinned by cross-checks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest, new
from pathlib import Path
from textwrap import dedent

import pytest

from app.services.short_token import (
    ShortTokenInvalid,
    issue_short_token,
    verify_short_token,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET = "platform-task-secret"
CLAIMS = {"eval_run_id": "eval_abc", "shard_id": "shard_1", "attempt": 1, "pool_id": "pool_x"}


def now() -> datetime:
    return datetime.now(UTC)


def test_issue_then_verify_roundtrip() -> None:
    token = issue_short_token(CLAIMS, SECRET, ttl_seconds=300, now=now())

    assert token.startswith("v1.")
    parts = token.split(".")
    assert len(parts) == 3
    claims = verify_short_token(token, SECRET)
    for key, value in CLAIMS.items():
        assert claims[key] == value
    assert claims["exp"] > datetime.now(UTC).timestamp()


def test_signature_covers_body_and_secret() -> None:
    token = issue_short_token(CLAIMS, SECRET, ttl_seconds=300, now=now())
    body = token.split(".")[1]
    expected = urlsafe_b64encode(
        new(SECRET.encode(), body.encode(), sha256).digest()
    ).decode().rstrip("=")
    assert token.split(".")[2] == expected

    with pytest.raises(ShortTokenInvalid):
        verify_short_token(token, "other-secret")
    tampered = f"v1.{body[:-1]}{'A' if body[-1] != 'A' else 'B'}.{token.split('.')[2]}"
    with pytest.raises(ShortTokenInvalid):
        verify_short_token(tampered, SECRET)


def test_expired_token_is_rejected() -> None:
    issued_at = datetime.now(UTC) - timedelta(seconds=600)
    token = issue_short_token(CLAIMS, SECRET, ttl_seconds=60, now=issued_at)
    with pytest.raises(ShortTokenInvalid):
        verify_short_token(token, SECRET)


def test_malformed_tokens_are_rejected() -> None:
    for bad in ("", "not-a-token", "v1.onlybody", "v2.a.b", "v1.!!!.!!!"):
        with pytest.raises(ShortTokenInvalid):
            verify_short_token(bad, SECRET)


def node_runner() -> str | None:
    return shutil.which("node")


def cross_script(mode: str) -> str:
    return dedent(
        f"""
        import {{ createToken, verifyToken }} from "{REPO_ROOT / 'runner' / 'lib' / 'auth.mjs'}";
        import {{ createInterface }} from "node:readline";
        const mode = "{mode}";
        const lines = createInterface({{ input: process.stdin }});
        let input = "";
        for await (const chunk of lines) input += chunk;
        const payload = JSON.parse(input);
        if (mode === "issue") {{
          const token = createToken(payload.claims, payload.secret, {{ ttlSeconds: payload.ttl_seconds, now: payload.now_ms }});
          process.stdout.write(token);
        }} else {{
          try {{
            verifyToken(payload.token, {{ secret: payload.secret, now: payload.now_ms }});
            process.stdout.write("ok");
          }} catch (error) {{
            process.stdout.write(`invalid:${{error.code ?? "unknown"}}`);
          }}
        }}
        """
    )


def run_node(mode: str, payload: dict) -> str:
    result = subprocess.run(
        [node_runner(), "--input-type=module", "-e", cross_script(mode)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.mark.skipif(node_runner() is None, reason="node is unavailable")
def test_python_verifies_node_issued_token() -> None:
    token = run_node(
        "issue",
        {"claims": CLAIMS, "secret": SECRET, "ttl_seconds": 300, "now_ms": int(now().timestamp() * 1000)},
    )
    claims = verify_short_token(token, SECRET)
    assert claims["eval_run_id"] == CLAIMS["eval_run_id"]
    assert claims["attempt"] == 1


@pytest.mark.skipif(node_runner() is None, reason="node is unavailable")
def test_node_verifies_python_issued_token() -> None:
    token = issue_short_token(CLAIMS, SECRET, ttl_seconds=300, now=now())
    result = run_node("verify", {"token": token, "secret": SECRET, "now_ms": int(now().timestamp() * 1000)})
    assert result == "ok"

    rejected = run_node(
        "verify", {"token": token, "secret": "wrong", "now_ms": int(now().timestamp() * 1000)}
    )
    assert rejected.startswith("invalid:")


def test_compare_digest_semantics_unchanged() -> None:
    # Guard the import surface used above: signature comparison must be constant-time.
    assert compare_digest(b"a", b"a") and not compare_digest(b"a", b"b")
    assert urlsafe_b64decode(urlsafe_b64encode(b"x")).rstrip(b"\x00") == b"x"

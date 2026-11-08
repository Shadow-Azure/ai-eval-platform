"""repository_dispatch client for the eval-shard GitHub workflow (M2-05).

One HTTP attempt per call — retry/backoff/lease policy lives in the outbox
dispatcher so it can persist reconciliation state. The transport is injectable
so unit tests never touch the network; production uses urllib with the
platform-configured ``GITHUB_TOKEN`` (repo scope on the host .env).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Callable

from ..config import Settings, get_settings

DispatchTransport = Callable[[str], tuple[int, str]]
# transport(url, *, method, body, headers, timeout) -> (status, body_text)


class GitHubDispatchError(Exception):
    """One dispatch attempt failed (HTTP error or transport failure)."""

    def __init__(self, message: str, *, status: int) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


def _default_transport(
    url: str, *, method: str, body: bytes, headers: dict[str, str], timeout: float
) -> tuple[int, str]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    # urllib.error.URLError / OSError bubble up and are mapped by the caller.


def post_repository_dispatch(
    *,
    event_type: str,
    client_payload: dict,
    settings: Settings | None = None,
    transport: DispatchTransport | None = None,
    now: datetime | None = None,
) -> dict:
    """POST one repository_dispatch; returns reconciliation info on success."""

    settings = settings or get_settings()
    if not settings.github_token:
        raise GitHubDispatchError("GITHUB_TOKEN 未配置，无法派发", status=0)

    url = f"{settings.github_api_url.rstrip('/')}/repos/{settings.github_repo}/dispatches"
    body = json.dumps({"event_type": event_type, "client_payload": client_payload}).encode()
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "ai-eval-platform-dispatcher",
    }

    try:
        if transport is not None:
            status, text = transport(url, method="POST", body=body, headers=headers, timeout=15)
        else:
            status, text = _default_transport(url, method="POST", body=body, headers=headers, timeout=15)
    except Exception as exc:
        raise GitHubDispatchError(f"GitHub 派发传输失败：{exc}", status=0) from exc

    if status < 200 or status >= 300:
        raise GitHubDispatchError(f"GitHub 派发失败：HTTP {status} {text.strip()[:200]}", status=status)

    now = now or datetime.now(UTC)
    return {
        "ok": True,
        "status": status,
        "dispatched_at": now.isoformat(timespec="microseconds"),
        "event_type": event_type,
    }

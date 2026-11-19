"""repository_dispatch / workflow-run client for the eval-shard workflow (M2-05).

One HTTP attempt per call — retry/backoff/lease policy lives in the outbox
dispatcher (and the cancellation reconciler, M4-02 A2) so it can persist
reconciliation state. The transport is injectable so unit tests never touch the
network; production uses urllib with the platform-configured ``GITHUB_TOKEN``
(repo scope on the host .env).

M4-02 A2 adds the two read/cancel calls the reconciler needs
(``find_workflow_run``, ``cancel_workflow_run``) using the same auth/URL
conventions as the dispatch POST.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from ..config import Settings, get_settings

DispatchTransport = Callable[[str], tuple[int, str]]
# transport(url, *, method, body, headers, timeout) -> (status, body_text)

# M8-51 (#323): GitHub rejects a repository_dispatch whose client_payload has
# more than 10 top-level properties or is 64KB or larger, and whose event_type
# is longer than 100 characters. Check the hard limits locally so a broken
# payload fails fast with a diagnosable error instead of burning the retry
# budget on a 422 that can never succeed. The byte budget keeps headroom under
# GitHub's 64KB hard limit.
DISPATCH_MAX_PAYLOAD_PROPERTIES = 10
DISPATCH_MAX_PAYLOAD_BYTES = 60 * 1024
DISPATCH_MAX_EVENT_TYPE_LENGTH = 100
DISPATCH_PAYLOAD_INVALID = "dispatch_payload_invalid"


class GitHubDispatchError(Exception):
    """One dispatch attempt failed (HTTP error or transport failure).

    ``retryable`` mirrors the outbox policy: transport errors, 429/5xx and a
    spammed 422 back off and retry, while 422 parameter validation is terminal.
    """

    def __init__(self, message: str, *, status: int, retryable: bool = True) -> None:
        self.status = status
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class DispatchPayloadInvalid(GitHubDispatchError):
    """The client_payload/event_type violates a GitHub dispatch limit.

    Raised by the local preflight before the transport is called; it means the
    payload can never be accepted, so the outbox must fail terminally instead
    of retrying.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"{DISPATCH_PAYLOAD_INVALID}: {detail}", status=0, retryable=False
        )


def _422_is_terminal(text: str) -> bool:
    """A 422 is terminal unless GitHub reports the endpoint as spammed.

    GitHub returns 422 both for parameter validation (property/size caps) and
    for "the endpoint has been spammed"; only the latter is transient.
    """

    return "spammed" not in (text or "").lower()


def validate_dispatch_request(*, event_type: str, client_payload: dict) -> bytes:
    """Enforce GitHub's repositories/dispatches limits before any network call.

    Returns the serialized client_payload so the caller reuses the exact bytes
    it validated. Raises :class:`DispatchPayloadInvalid` with the concrete
    property count/size so the failure is diagnosable.
    """

    if not isinstance(event_type, str) or not event_type.strip():
        raise DispatchPayloadInvalid("event_type 必须是非空字符串")
    if len(event_type) > DISPATCH_MAX_EVENT_TYPE_LENGTH:
        raise DispatchPayloadInvalid(
            f"event_type 长度 {len(event_type)} 超过 {DISPATCH_MAX_EVENT_TYPE_LENGTH} 字符"
        )
    if not isinstance(client_payload, dict):
        raise DispatchPayloadInvalid(
            f"client_payload 必须是 JSON object，实际是 {type(client_payload).__name__}"
        )
    properties = len(client_payload)
    if properties > DISPATCH_MAX_PAYLOAD_PROPERTIES:
        raise DispatchPayloadInvalid(
            f"client_payload 顶层属性 {properties} 个，超过 GitHub 上限 "
            f"{DISPATCH_MAX_PAYLOAD_PROPERTIES}"
        )
    serialized = json.dumps(client_payload, ensure_ascii=False).encode("utf-8")
    if len(serialized) > DISPATCH_MAX_PAYLOAD_BYTES:
        raise DispatchPayloadInvalid(
            f"client_payload 序列化 {len(serialized)} 字节，超过本地护栏 "
            f"{DISPATCH_MAX_PAYLOAD_BYTES} 字节（GitHub 硬上限 64KB）"
        )
    return serialized


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


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "ai-eval-platform-dispatcher",
    }


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

    # M8-51 (#323): fail before the transport call when the payload can never
    # satisfy GitHub's documented limits.
    validate_dispatch_request(event_type=event_type, client_payload=client_payload)

    url = f"{settings.github_api_url.rstrip('/')}/repos/{settings.github_repo}/dispatches"
    # Serialize exactly like the preflight so the validated byte count is the
    # byte count GitHub receives (escaped non-ASCII would inflate it).
    body = json.dumps(
        {"event_type": event_type, "client_payload": client_payload},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = _headers(settings)

    try:
        if transport is not None:
            status, text = transport(url, method="POST", body=body, headers=headers, timeout=15)
        else:
            status, text = _default_transport(url, method="POST", body=body, headers=headers, timeout=15)
    except Exception as exc:
        raise GitHubDispatchError(f"GitHub 派发传输失败：{exc}", status=0) from exc

    if status < 200 or status >= 300:
        message = f"GitHub 派发失败：HTTP {status} {text.strip()[:200]}"
        raise GitHubDispatchError(
            message,
            status=status,
            retryable=not (status == 422 and _422_is_terminal(text)),
        )

    now = now or datetime.now(UTC)
    return {
        "ok": True,
        "status": status,
        "dispatched_at": now.isoformat(timespec="microseconds"),
        "event_type": event_type,
    }


def find_workflow_run(
    eval_run_id: str,
    *,
    settings: Settings | None = None,
    transport: DispatchTransport | None = None,
    timeout: float = 10,
) -> str | None:
    """Find the dispatched workflow run for ``eval_run_id`` (M4-02 A2).

    Cancellation needs the GitHub run id to POST /cancel, and the dispatch
    response does not carry it. The workflow's ``run-name`` embeds the
    dispatch_key (which starts with the eval run id), so the newest matching
    ``display_title``/``name`` of a repository_dispatch run is ours.
    """

    settings = settings or get_settings()
    if not settings.github_token:
        raise GitHubDispatchError("GITHUB_TOKEN 未配置，无法查询工作流运行", status=0)

    url = (
        f"{settings.github_api_url.rstrip('/')}/repos/{settings.github_repo}"
        "/actions/runs?event=repository_dispatch&per_page=100"
    )
    headers = _headers(settings)
    try:
        if transport is not None:
            status, text = transport(url, method="GET", body=b"", headers=headers, timeout=timeout)
        else:
            status, text = _default_transport(url, method="GET", body=b"", headers=headers, timeout=timeout)
    except Exception as exc:
        raise GitHubDispatchError(f"GitHub 工作流查询传输失败：{exc}", status=0) from exc

    if status < 200 or status >= 300:
        raise GitHubDispatchError(
            f"GitHub 工作流查询失败：HTTP {status} {text.strip()[:200]}", status=status
        )
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise GitHubDispatchError("GitHub 工作流查询返回非 JSON", status=status) from exc

    # The REST API returns the newest run first; keep the first id match.
    for item in payload.get("workflow_runs") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("display_title") or "")
        name = str(item.get("name") or "")
        if eval_run_id not in title and eval_run_id not in name:
            continue
        if item.get("id") is not None:
            return str(item["id"])
    return None


def cancel_workflow_run(
    github_run_id: str,
    *,
    settings: Settings | None = None,
    transport: DispatchTransport | None = None,
    timeout: float = 10,
) -> None:
    """POST the GitHub cancel endpoint for one workflow run (M4-02 A2).

    Accepted status codes are the documented 202 (cancellation queued) and 204
    (already cancelling/finished); anything else stays a retryable error.
    """

    settings = settings or get_settings()
    if not settings.github_token:
        raise GitHubDispatchError("GITHUB_TOKEN 未配置，无法取消工作流", status=0)

    url = (
        f"{settings.github_api_url.rstrip('/')}/repos/{settings.github_repo}"
        f"/actions/runs/{github_run_id}/cancel"
    )
    headers = _headers(settings)
    try:
        if transport is not None:
            status, text = transport(url, method="POST", body=b"", headers=headers, timeout=timeout)
        else:
            status, text = _default_transport(url, method="POST", body=b"", headers=headers, timeout=timeout)
    except Exception as exc:
        raise GitHubDispatchError(f"GitHub 取消工作流传输失败：{exc}", status=0) from exc

    if status not in (202, 204):
        raise GitHubDispatchError(
            f"GitHub 取消工作流失败：HTTP {status} {text.strip()[:200]}", status=status
        )


def list_workflow_runs_for_commit(
    commit: str,
    *,
    settings: Settings | None = None,
    transport: DispatchTransport | None = None,
    timeout: float = 5,
) -> list[dict]:
    """Return the Actions workflow runs for one commit (M5-18 quality gate).

    The deployment page shows the real test/docs gate for the deployed commit;
    a missing token or a failed probe is handled by the caller as ``unknown``,
    never as success.
    """

    settings = settings or get_settings()
    if not settings.github_token:
        raise GitHubDispatchError("GITHUB_TOKEN 未配置，无法查询 CI 门禁", status=0)

    url = (
        f"{settings.github_api_url.rstrip('/')}/repos/{settings.github_repo}"
        f"/actions/runs?head_sha={commit}&per_page=50"
    )
    headers = _headers(settings)
    try:
        if transport is not None:
            status, text = transport(url, method="GET", body=b"", headers=headers, timeout=timeout)
        else:
            status, text = _default_transport(
                url, method="GET", body=b"", headers=headers, timeout=timeout
            )
    except Exception as exc:
        raise GitHubDispatchError(f"GitHub CI 查询传输失败：{exc}", status=0) from exc

    if status < 200 or status >= 300:
        raise GitHubDispatchError(
            f"GitHub CI 查询失败：HTTP {status} {text.strip()[:200]}", status=status
        )
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise GitHubDispatchError("GitHub CI 查询返回非 JSON", status=status) from exc
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise GitHubDispatchError("GitHub CI 查询缺少 workflow_runs", status=status)
    return [run for run in runs if isinstance(run, dict)]

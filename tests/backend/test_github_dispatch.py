"""repository_dispatch client for the GitHub eval-shard workflow (M2-05).

The dispatcher worker owns retries/backoff; this module performs exactly one
HTTP attempt per call and reports a structured result so the outbox loop can
persist reconciliation info (status, attempts, last error).
"""

from __future__ import annotations

import json

import pytest
from app.config import get_settings

from app.services.github_dispatch import (
    GitHubDispatchError,
    post_repository_dispatch,
)


class FakeTransport:
    def __init__(self, responses: list[tuple[int, str] | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url: str, *, method: str, body: bytes, headers: dict[str, str], timeout: float):
        self.calls.append(
            {"url": url, "method": method, "body": json.loads(body.decode()), "headers": headers, "timeout": timeout}
        )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def configure(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    values = {
        "GITHUB_TOKEN": "gh-token",
        "GITHUB_REPO": "Shadow-Azure/ai-eval-platform",
        "GITHUB_API_URL": "https://api.github.com",
        **overrides,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def restore_settings(monkeypatch: pytest.MonkeyPatch):
    yield
    get_settings.cache_clear()


def test_posts_event_type_and_payload_with_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([(204, "")])

    result = post_repository_dispatch(
        event_type="eval-shard",
        client_payload={"dispatch_key": "eval:shard:1"},
        transport=transport,
    )

    assert result["ok"] is True
    assert result["status"] == 204
    assert "dispatched_at" in result
    call = transport.calls[0]
    assert call["url"] == "https://api.github.com/repos/Shadow-Azure/ai-eval-platform/dispatches"
    assert call["method"] == "POST"
    assert call["headers"]["Authorization"] == "Bearer gh-token"
    assert call["headers"]["Accept"] == "application/vnd.github+json"
    assert call["body"] == {"event_type": "eval-shard", "client_payload": {"dispatch_key": "eval:shard:1"}}


def test_http_error_raises_structured_dispatch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([(403, '{"message":"Resource not accessible"}')])

    with pytest.raises(GitHubDispatchError) as excinfo:
        post_repository_dispatch(event_type="eval-shard", client_payload={}, transport=transport)
    assert excinfo.value.status == 403
    assert "Resource not accessible" in excinfo.value.message


def test_transport_failure_maps_to_status_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([OSError("connection reset")])

    with pytest.raises(GitHubDispatchError) as excinfo:
        post_repository_dispatch(event_type="eval-shard", client_payload={}, transport=transport)
    assert excinfo.value.status == 0
    assert "connection reset" in excinfo.value.message


def test_missing_token_is_a_dispatch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, GITHUB_TOKEN="")
    transport = FakeTransport([])

    with pytest.raises(GitHubDispatchError) as excinfo:
        post_repository_dispatch(event_type="eval-shard", client_payload={}, transport=transport)
    assert excinfo.value.status == 0
    assert "token" in excinfo.value.message.lower()
    assert transport.calls == []

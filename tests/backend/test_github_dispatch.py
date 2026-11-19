"""repository_dispatch / workflow-run client for eval-shard (M2-05, M4-02 A2).

The dispatcher worker owns retries/backoff; this module performs exactly one
HTTP attempt per call and reports a structured result so the outbox loop can
persist reconciliation info (status, attempts, last error). M4-02 A2 adds the
workflow-run lookup and cancel calls used by the cancellation reconciler.
"""

from __future__ import annotations

import json

import pytest
from app.config import get_settings
from app.services.github_dispatch import (
    DispatchPayloadInvalid,
    GitHubDispatchError,
    cancel_workflow_run,
    find_workflow_run,
    post_repository_dispatch,
    validate_dispatch_request,
)


class FakeTransport:
    def __init__(self, responses: list[tuple[int, str] | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url: str, *, method: str, body: bytes, headers: dict[str, str], timeout: float):
        self.calls.append(
            {
                "url": url,
                "method": method,
                "body": json.loads(body.decode()) if body else None,
                "headers": headers,
                "timeout": timeout,
            }
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


# ---------------------------------------------------------------------------
# M8-51 (#323): local transport guard + 422 classification
# ---------------------------------------------------------------------------


def test_preflight_rejects_more_than_ten_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([])
    payload = {f"key_{index}": index for index in range(11)}

    with pytest.raises(DispatchPayloadInvalid) as excinfo:
        post_repository_dispatch(
            event_type="eval-shard", client_payload=payload, transport=transport
        )

    assert transport.calls == []
    assert excinfo.value.retryable is False
    assert "11" in excinfo.value.message
    assert "超过 GitHub 上限 10" in excinfo.value.message


def test_preflight_rejects_a_payload_at_or_above_the_byte_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch)
    transport = FakeTransport([])
    payload = {"manifest_url": "http://x/" + "a" * (60 * 1024)}

    with pytest.raises(DispatchPayloadInvalid) as excinfo:
        post_repository_dispatch(
            event_type="eval-shard", client_payload=payload, transport=transport
        )

    assert transport.calls == []
    assert excinfo.value.retryable is False
    assert "字节" in excinfo.value.message


def test_preflight_rejects_a_non_object_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([])

    with pytest.raises(DispatchPayloadInvalid):
        post_repository_dispatch(
            event_type="eval-shard", client_payload=["not", "an", "object"], transport=transport
        )

    assert transport.calls == []


def test_preflight_rejects_an_overlong_event_type(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([])

    with pytest.raises(DispatchPayloadInvalid) as excinfo:
        post_repository_dispatch(
            event_type="e" * 101, client_payload={"dispatch_key": "k"}, transport=transport
        )

    assert transport.calls == []
    assert "101" in excinfo.value.message


def test_preflight_accepts_exactly_ten_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([(204, "")])

    validate_dispatch_request(
        event_type="eval-shard",
        client_payload={f"key_{index}": index for index in range(10)},
    )
    result = post_repository_dispatch(
        event_type="eval-shard",
        client_payload={f"key_{index}": index for index in range(10)},
        transport=transport,
    )

    assert result["ok"] is True
    assert len(transport.calls) == 1


def test_422_parameter_error_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport(
        [(422, '{"message":"No more than 10 properties are allowed; 11 were supplied."}')]
    )

    with pytest.raises(GitHubDispatchError) as excinfo:
        post_repository_dispatch(
            event_type="eval-shard", client_payload={"dispatch_key": "k"}, transport=transport
        )

    assert excinfo.value.status == 422
    assert excinfo.value.retryable is False


def test_422_spammed_error_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([(422, '{"message":"the endpoint has been spammed"}')])

    with pytest.raises(GitHubDispatchError) as excinfo:
        post_repository_dispatch(
            event_type="eval-shard", client_payload={"dispatch_key": "k"}, transport=transport
        )

    assert excinfo.value.status == 422
    assert excinfo.value.retryable is True


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_other_dispatch_errors_stay_retryable(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    configure(monkeypatch)
    transport = FakeTransport([(status, '{"message":"transient"}')])

    with pytest.raises(GitHubDispatchError) as excinfo:
        post_repository_dispatch(
            event_type="eval-shard", client_payload={"dispatch_key": "k"}, transport=transport
        )

    assert excinfo.value.retryable is True


def test_dispatch_schema_caps_top_level_properties() -> None:
    from app.runner_contracts import ContractInvalid, validate_payload

    payload = {
        "schema_version": "1.0",
        "eval_run_id": "run-1",
        "shard_id": "shard_000",
        "attempt": 1,
        "dispatch_key": "run-1:shard_000:1",
        "manifest_url": "http://127.0.0.1:8000/runner/v1/shards/shard_000/manifest?x=1",
        "manifest_digest": "sha256:" + "a" * 64,
        "runs_on": ["self-hosted", "linux", "eval"],
        "job_timeout_minutes": 70,
        "expires_at": "2026-11-19T00:00:00Z",
    }
    validate_payload("dispatch", payload)

    with pytest.raises(ContractInvalid) as excinfo:
        validate_payload("dispatch", {**payload, "scripts_bundle": {"name": "x"}})

    assert "最多 10 个属性" in str(excinfo.value)
    assert "不允许的额外字段" in str(excinfo.value)



# ---------------------------------------------------------------------------
# Workflow-run lookup + cancel (M4-02 A2)
# ---------------------------------------------------------------------------


def test_find_workflow_run_matches_display_title_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch)
    transport = FakeTransport(
        [
            (
                200,
                json.dumps(
                    {
                        "workflow_runs": [
                            {"id": 11, "display_title": "eval-shard other-run:shard_000:1"},
                            {"id": 22, "display_title": "eval-shard run-abc:shard_000:1", "name": "eval-shard"},
                        ]
                    }
                ),
            )
        ]
    )

    assert find_workflow_run("run-abc", transport=transport) == "22"
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == (
        "https://api.github.com/repos/Shadow-Azure/ai-eval-platform"
        "/actions/runs?event=repository_dispatch&per_page=100"
    )
    assert call["headers"]["Authorization"] == "Bearer gh-token"
    assert call["headers"]["Accept"] == "application/vnd.github+json"


def test_find_workflow_run_falls_back_to_name_and_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch)
    transport = FakeTransport(
        [
            (200, json.dumps({"workflow_runs": [{"id": 7, "name": "eval-shard run-xyz"}]})),
            (200, json.dumps({"workflow_runs": []})),
        ]
    )

    assert find_workflow_run("run-xyz", transport=transport) == "7"
    assert find_workflow_run("run-missing", transport=transport) is None


def test_find_workflow_run_maps_http_and_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch)
    http_error = FakeTransport([(401, '{"message":"Bad credentials"}')])
    with pytest.raises(GitHubDispatchError) as excinfo:
        find_workflow_run("run-1", transport=http_error)
    assert excinfo.value.status == 401

    transport_error = FakeTransport([OSError("connection reset")])
    with pytest.raises(GitHubDispatchError) as excinfo:
        find_workflow_run("run-1", transport=transport_error)
    assert excinfo.value.status == 0


def test_cancel_workflow_run_accepts_202_and_204(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([(202, ""), (204, "")])

    cancel_workflow_run("4242", transport=transport)
    cancel_workflow_run("4242", transport=transport)

    for call in transport.calls:
        assert call["url"] == (
            "https://api.github.com/repos/Shadow-Azure/ai-eval-platform"
            "/actions/runs/4242/cancel"
        )
        assert call["method"] == "POST"
        assert call["headers"]["Authorization"] == "Bearer gh-token"


def test_cancel_workflow_run_maps_other_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    transport = FakeTransport([(422, '{"message":"Cannot cancel"}')])

    with pytest.raises(GitHubDispatchError) as excinfo:
        cancel_workflow_run("4242", transport=transport)
    assert excinfo.value.status == 422
    assert "Cannot cancel" in excinfo.value.message


def test_workflow_run_calls_require_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, GITHUB_TOKEN="")
    transport = FakeTransport([])

    with pytest.raises(GitHubDispatchError):
        find_workflow_run("run-1", transport=transport)
    with pytest.raises(GitHubDispatchError):
        cancel_workflow_run("1", transport=transport)
    assert transport.calls == []

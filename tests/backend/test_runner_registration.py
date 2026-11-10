"""M4-04 GitHub ephemeral runner registration client.

The registrar is the only component allowed to call GitHub's runner-admin
endpoints. Tests use a recording transport and assert there is no credential
or JIT leakage.
"""

from __future__ import annotations

import json

import pytest
from app.config import Settings
from app.services.provisioning import ProvisionerError
from app.services.runner_registration import GitHubRunnerRegistrar


class RecordingTransport:
    def __init__(self, responses: list[tuple[int, dict | str]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url: str, *, method: str, body: bytes, headers: dict, timeout: float):
        payload = json.loads(body) if body else None
        self.calls.append({"url": url, "method": method, "payload": payload, "headers": headers})
        status, response = self.responses.pop(0)
        text = response if isinstance(response, str) else json.dumps(response)
        return status, text


def _settings(**overrides) -> Settings:
    values = {
        "runner_admin_token": "admin-token",
        "github_repo": "Shadow-Azure/ai-eval-platform",
        "github_api_url": "https://api.github.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_generate_jitconfig_posts_name_and_labels() -> None:
    transport = RecordingTransport(
        [
            (
                201,
                {
                    "runner": {"id": 7, "name": "spot-abc"},
                    "encoded_jit_config": "encoded-secret",
                },
            )
        ]
    )
    registrar = GitHubRunnerRegistrar(_settings(), transport=transport)

    jit = registrar.generate_jitconfig(name="spot-abc", labels=["self-hosted", "spot"])

    assert jit.runner_id == 7
    assert jit.name == "spot-abc"
    assert jit.encoded == "encoded-secret"
    call = transport.calls[0]
    assert call["url"].endswith("/repos/Shadow-Azure/ai-eval-platform/actions/runners/generate-jitconfig")
    assert call["method"] == "POST"
    assert call["payload"] == {
        "name": "spot-abc",
        "labels": ["self-hosted", "spot"],
        "runner_group_id": 1,
    }
    assert call["headers"]["Authorization"] == "Bearer admin-token"


def test_generate_jitconfig_requires_admin_token() -> None:
    registrar = GitHubRunnerRegistrar(_settings(runner_admin_token=None), transport=RecordingTransport([]))
    with pytest.raises(ProvisionerError) as caught:
        registrar.generate_jitconfig(name="spot-abc", labels=["self-hosted"])
    assert caught.value.code == "runner_registration_unavailable"


def test_generate_jitconfig_forbidden_is_diagnosable() -> None:
    transport = RecordingTransport([(403, {"message": "Must have admin rights"})])
    registrar = GitHubRunnerRegistrar(_settings(), transport=transport)
    with pytest.raises(ProvisionerError) as caught:
        registrar.generate_jitconfig(name="spot-abc", labels=["self-hosted"])
    assert caught.value.code == "runner_registration_forbidden"
    assert "admin-token" not in str(caught.value)


def test_delete_runner_by_name_lists_then_deletes() -> None:
    transport = RecordingTransport(
        [
            (200, {"runners": [{"id": 7, "name": "spot-abc"}, {"id": 8, "name": "other"}]}),
            (204, ""),
        ]
    )
    registrar = GitHubRunnerRegistrar(_settings(), transport=transport)

    deleted = registrar.delete_runner(name="spot-abc")

    assert deleted is True
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[1]["method"] == "DELETE"
    assert transport.calls[1]["url"].endswith("/actions/runners/7")


def test_delete_missing_runner_is_success() -> None:
    transport = RecordingTransport([(200, {"runners": []})])
    registrar = GitHubRunnerRegistrar(_settings(), transport=transport)
    assert registrar.delete_runner(name="spot-missing") is False


def test_delete_404_is_success() -> None:
    transport = RecordingTransport([(404, {"message": "Not Found"})])
    registrar = GitHubRunnerRegistrar(_settings(), transport=transport)
    assert registrar.delete_runner(runner_id=9) is False

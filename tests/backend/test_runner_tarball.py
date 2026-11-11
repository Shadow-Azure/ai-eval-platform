"""M5-11: the fixed runner-tarball proxy streams the cached OSS object.

The deploy pipeline still caches the archive, but cloud-init downloads from
`/runner/v1/runner-tarball/<version>.tar.gz`: the API reads the private object
with platform credentials, so the address never expires with a presigned URL.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.api import runners as runners_api
from app.services.object_store import ObjectMissing, ObjectStoreError
from fastapi.testclient import TestClient

PAYLOAD = b"\x1f\x8b" + b"runner-archive-bytes" * 16
TARBALL_URL = "/runner/v1/runner-tarball/2.338.2.tar.gz"


class FakeStore:
    def __init__(
        self,
        payload: bytes = PAYLOAD,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.headed: list[str] = []
        self.streamed: list[str] = []

    def head_object(self, key: str) -> int:
        self.headed.append(key)
        if self.error is not None:
            raise self.error
        return len(self.payload)

    def iter_object(self, key: str) -> Iterator[bytes]:
        self.streamed.append(key)

        def chunks():
            for index in range(0, len(self.payload), 7):
                yield self.payload[index : index + 7]

        return chunks()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch):
    def _install(fake: FakeStore) -> FakeStore:
        monkeypatch.setattr(runners_api, "object_store_from_settings", lambda _settings: fake)
        return fake

    return _install


def test_streams_the_cached_archive_with_the_runner_token(
    client: TestClient, configure_settings, store
) -> None:
    configure_settings(RUNNER_HEARTBEAT_TOKEN="platform-side-token")
    fake = store(FakeStore())

    response = client.get(
        TARBALL_URL, headers={"Authorization": "Bearer platform-side-token"}
    )

    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["content-type"] == "application/gzip"
    assert response.headers["content-length"] == str(len(PAYLOAD))
    assert 'filename="2.338.2.tar.gz"' in response.headers["content-disposition"]
    assert fake.headed == ["runner/actions-runner-linux-x64-2.338.2.tar.gz"]
    assert fake.streamed == fake.headed


def test_requires_the_runner_token_when_configured(
    client: TestClient, configure_settings, store
) -> None:
    configure_settings(RUNNER_HEARTBEAT_TOKEN="platform-side-token")
    store(FakeStore())

    missing = client.get(TARBALL_URL)
    wrong = client.get(TARBALL_URL, headers={"Authorization": "Bearer not-the-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json()["detail"]["error"]["code"] == "runner_token_invalid"


def test_rejects_a_filename_that_is_not_a_version(client: TestClient, store) -> None:
    store(FakeStore())

    response = client.get("/runner/v1/runner-tarball/latest.tar.gz")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "runner_tarball_not_found"


def test_unknown_version_returns_not_found(client: TestClient, store) -> None:
    store(FakeStore(error=ObjectMissing("runner/actions-runner-linux-x64-9.9.9.tar.gz")))

    response = client.get("/runner/v1/runner-tarball/9.9.9.tar.gz")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "runner_tarball_not_found"


def test_unconfigured_object_store_returns_unavailable(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(runners_api, "object_store_from_settings", lambda _settings: None)

    response = client.get(TARBALL_URL)

    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "runner_tarball_unavailable"


def test_object_store_failure_returns_bad_gateway(client: TestClient, store) -> None:
    store(FakeStore(error=ObjectStoreError("boom")))

    response = client.get(TARBALL_URL)

    assert response.status_code == 502
    assert response.json()["detail"]["error"]["code"] == "runner_tarball_unavailable"


def test_error_paths_do_not_leak_the_heartbeat_token(
    client: TestClient, configure_settings, store
) -> None:
    configure_settings(RUNNER_HEARTBEAT_TOKEN="platform-side-token")
    store(FakeStore(error=ObjectMissing("missing")))
    response = client.get(TARBALL_URL)
    assert "platform-side-token" not in response.text

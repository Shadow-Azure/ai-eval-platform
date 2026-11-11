"""M5-12: runner scripts metadata endpoint + private OSS proxy."""

from __future__ import annotations

from collections.abc import Iterator

from app.api import runners as runners_api
from app.models import Outbox
from app.services.object_store import ObjectMissing, ObjectStoreError
from app.services.short_token import issue_short_token, verify_short_token
from case_helpers import SECRET
from fastapi.testclient import TestClient

NAME = "eval-runner-scripts-0123456789abcdef0123456789abcdef.tar.gz"
PAYLOAD = b"\x1f\x8b" + b"scripts-bundle" * 4000
BASE = "/runner/v1/shards/{shard}/scripts"


class FakeStore:
    def __init__(self, payload: bytes = PAYLOAD, error: Exception | None = None) -> None:
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
            for index in range(0, len(self.payload), 11):
                yield self.payload[index : index + 11]

        return chunks()


def _install_store(monkeypatch, fake: FakeStore) -> FakeStore:
    monkeypatch.setattr(runners_api, "object_store_from_settings", lambda _settings: fake)
    return fake


def _bundle_settings(configure_settings, *, md5: str = "0" * 32) -> None:
    configure_settings(
        RUNNER_TASK_SECRET=SECRET,
        RUNNER_SCRIPTS_BUNDLE_NAME=NAME,
        RUNNER_SCRIPTS_BUNDLE_MD5=md5,
        RUNNER_SCRIPTS_BUNDLE_SHA256="a" * 64,
        RUNNER_SCRIPTS_BUNDLE_SIZE=len(PAYLOAD),
    )


def test_metadata_returns_url_digest_and_scoped_token(
    eval_env, client: TestClient, configure_settings, monkeypatch
) -> None:
    env = eval_env
    _bundle_settings(configure_settings)
    _install_store(monkeypatch, FakeStore())
    dispatch_key = env["session"].query(Outbox).first().dispatch_key

    response = client.get(
        BASE.format(shard=env["shard"]),
        params={"eval_run_id": env["run_id"], "attempt": 1, "dispatch_key": dispatch_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == NAME
    assert body["md5"] == "0" * 32
    assert body["sha256"] == "a" * 64
    assert body["size"] == len(PAYLOAD)
    assert body["url"].endswith(
        f"/runner/v1/scripts/{NAME}?eval_run_id={env['run_id']}"
        f"&shard_id={env['shard']}&attempt=1"
    )
    claims = verify_short_token(body["token"], SECRET)
    assert claims["scope"] == "runner_scripts"
    assert claims["eval_run_id"] == env["run_id"]
    assert claims["shard_id"] == env["shard"]
    assert claims["attempt"] == 1


def test_metadata_rejects_forged_dispatch_key(eval_env, client: TestClient) -> None:
    response = client.get(
        BASE.format(shard=eval_env["shard"]),
        params={"eval_run_id": eval_env["run_id"], "attempt": 1, "dispatch_key": "forged"},
    )
    assert response.status_code == 404


def test_proxy_streams_with_the_scoped_token(client: TestClient, configure_settings, monkeypatch) -> None:
    configure_settings(RUNNER_TASK_SECRET=SECRET)
    fake = _install_store(monkeypatch, FakeStore())
    token = issue_short_token(
        {"eval_run_id": "run-1", "shard_id": "shard_000", "attempt": 1, "bundle": NAME, "scope": "runner_scripts"},
        SECRET,
    )

    response = client.get(
        f"/runner/v1/scripts/{NAME}",
        params={"eval_run_id": "run-1", "shard_id": "shard_000", "attempt": 1},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["content-type"] == "application/gzip"
    assert response.headers["content-length"] == str(len(PAYLOAD))
    assert fake.headed == [f"runner/scripts/{NAME}"]
    assert fake.streamed == fake.headed


def test_proxy_requires_a_token_when_configured(client: TestClient, configure_settings) -> None:
    configure_settings(RUNNER_TASK_SECRET=SECRET)
    response = client.get(
        f"/runner/v1/scripts/{NAME}",
        params={"eval_run_id": "run-1", "shard_id": "shard_000", "attempt": 1},
    )
    assert response.status_code == 401


def test_proxy_rejects_wrong_scope_or_binding(client: TestClient, configure_settings, monkeypatch) -> None:
    configure_settings(RUNNER_TASK_SECRET=SECRET)
    _install_store(monkeypatch, FakeStore())
    no_scope = issue_short_token({"eval_run_id": "run-1", "shard_id": "shard_000", "attempt": 1}, SECRET)
    wrong = issue_short_token(
        {
            "eval_run_id": "run-2",
            "shard_id": "shard_000",
            "attempt": 1,
            "bundle": NAME,
            "scope": "runner_scripts",
        },
        SECRET,
    )
    params = {"eval_run_id": "run-1", "shard_id": "shard_000", "attempt": 1}

    other_bundle = issue_short_token(
        {
            "eval_run_id": "run-1",
            "shard_id": "shard_000",
            "attempt": 1,
            "bundle": "eval-runner-scripts-ffffffffffffffffffffffffffffffff.tar.gz",
            "scope": "runner_scripts",
        },
        SECRET,
    )
    for token in (no_scope, wrong, other_bundle):
        response = client.get(
            f"/runner/v1/scripts/{NAME}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


def test_proxy_error_paths(client: TestClient, configure_settings, monkeypatch) -> None:
    configure_settings(RUNNER_TASK_SECRET=SECRET)
    token = issue_short_token(
        {"eval_run_id": "run-1", "shard_id": "shard_000", "attempt": 1, "bundle": NAME, "scope": "runner_scripts"},
        SECRET,
    )
    params = {"eval_run_id": "run-1", "shard_id": "shard_000", "attempt": 1}
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/runner/v1/scripts/latest.tar.gz", params=params, headers=headers).status_code == 404

    _install_store(monkeypatch, FakeStore(error=ObjectMissing("missing")))
    assert client.get(f"/runner/v1/scripts/{NAME}", params=params, headers=headers).status_code == 404

    _install_store(monkeypatch, FakeStore(error=ObjectStoreError("boom")))
    assert client.get(f"/runner/v1/scripts/{NAME}", params=params, headers=headers).status_code == 502

    monkeypatch.setattr(runners_api, "object_store_from_settings", lambda _settings: None)
    assert client.get(f"/runner/v1/scripts/{NAME}", params=params, headers=headers).status_code == 503


def test_metadata_prefers_the_bundle_pinned_on_dispatch(
    eval_env, client: TestClient, configure_settings, monkeypatch
) -> None:
    env = eval_env
    _bundle_settings(configure_settings)
    pinned_name = "eval-runner-scripts-ffffffffffffffffffffffffffffffff.tar.gz"
    row = env["session"].query(Outbox).first()
    row.payload_json = {
        **row.payload_json,
        "scripts_bundle": {
            "name": pinned_name,
            "md5": "f" * 32,
            "sha256": "b" * 64,
            "size": len(PAYLOAD),
        },
    }
    env["session"].commit()
    _install_store(monkeypatch, FakeStore())

    response = client.get(
        BASE.format(shard=env["shard"]),
        params={"eval_run_id": env["run_id"], "attempt": 1, "dispatch_key": row.dispatch_key},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == pinned_name
    assert response.json()["md5"] == "f" * 32

"""M9 (#263) self-evolution launch configuration: prepare, create, echo.

The wizard's optional reporting target must round-trip prepare → create with
the four eval_run columns set, the workspace token stored only as Fernet
ciphertext, and no API response ever carrying the plaintext (not even a
masked form).
"""

from __future__ import annotations

from app.models import EvalPreparation, EvalRun
from app.secrets import get_secret_box
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_preparations import prepare_body, seed_launch

TOKEN = "whk_" + "a1B2c3D4e5" * 3
CONFIG = {
    "enabled": True,
    "base_url": "https://he.example.com",
    "workspace_id": "ws-prod-01",
    "token": TOKEN,
}


def _prepare(client: TestClient, ids: dict, self_evolution: dict | None) -> dict:
    body = prepare_body(ids)
    if self_evolution is not None:
        body["self_evolution"] = self_evolution
    response = client.post("/api/v1/evaluations/prepare", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _create(client: TestClient, preparation_id: str, key: str) -> dict:
    response = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": preparation_id},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_prepare_rejects_enabled_with_missing_fields(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    for partial in (
        {"enabled": True},
        {"enabled": True, "base_url": "https://he.example.com"},
        {"enabled": True, "base_url": "https://he.example.com", "workspace_id": "ws-1"},
    ):
        response = client.post(
            "/api/v1/evaluations/prepare",
            json=prepare_body(ids, self_evolution=partial),
        )
        assert response.status_code == 422, (partial, response.text)


def test_prepare_rejects_bad_token_and_url_and_workspace(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    bad_configs = [
        {**CONFIG, "token": "gk_abc123"},
        {**CONFIG, "token": "whk_"},
        {**CONFIG, "base_url": "ftp://he.example.com"},
        {**CONFIG, "workspace_id": "-ws"},
        {**CONFIG, "workspace_id": "ws 1"},
    ]
    for config in bad_configs:
        response = client.post(
            "/api/v1/evaluations/prepare",
            json=prepare_body(ids, self_evolution=config),
        )
        assert response.status_code == 422, (config, response.text)


def test_prepare_rejects_disabled_with_fields(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(
            ids,
            self_evolution={
                "enabled": False,
                "base_url": "https://he.example.com",
                "workspace_id": "ws-prod-01",
                "token": TOKEN,
            },
        ),
    )
    assert response.status_code == 422, response.text


def test_prepare_create_freezes_target_and_encrypts_token(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    body = _prepare(client, ids, CONFIG)
    assert body["ready"] is True
    assert body["self_evolution"] == {
        "enabled": True,
        "base_url": "https://he.example.com",
        "workspace_id": "ws-prod-01",
    }
    assert TOKEN not in body["summary"].__str__()

    preparation = session.get(EvalPreparation, body["preparation_id"])
    assert preparation is not None
    assert preparation.snapshot_json["self_evolution"] == {
        "enabled": True,
        "base_url": "https://he.example.com",
        "workspace_id": "ws-prod-01",
    }
    # The token lives only as Fernet ciphertext on the row, never in JSON.
    assert TOKEN not in preparation.snapshot_json.__str__()
    assert preparation.self_evolution_token_encrypted
    assert TOKEN not in preparation.self_evolution_token_encrypted
    box = get_secret_box()
    assert box.decrypt(preparation.self_evolution_token_encrypted, box.version) == TOKEN

    run_body = _create(client, body["preparation_id"], "idem-swe-1")
    assert run_body["self_evolution_enabled"] is True
    assert run_body["self_evolution_base_url"] == "https://he.example.com"
    assert run_body["self_evolution_workspace_id"] == "ws-prod-01"

    run = session.get(EvalRun, run_body["id"])
    assert run is not None
    assert run.self_evolution_enabled is True
    assert run.self_evolution_base_url == "https://he.example.com"
    assert run.self_evolution_workspace_id == "ws-prod-01"
    assert run.self_evolution_token_encrypted
    assert TOKEN not in run.self_evolution_token_encrypted
    assert box.decrypt(run.self_evolution_token_encrypted, box.version) == TOKEN

    detail = client.get(f"/api/v1/evaluations/{run.id}")
    assert detail.status_code == 200, detail.text
    detail_text = detail.text
    assert detail_text.count(TOKEN) == 0
    assert "self_evolution_token" not in detail_text
    assert detail.json()["self_evolution_workspace_id"] == "ws-prod-01"


def test_prepare_without_config_keeps_disabled_defaults(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    body = _prepare(client, ids, None)
    assert body["ready"] is True
    assert body["self_evolution"] == {"enabled": False, "base_url": None, "workspace_id": None}

    preparation = session.get(EvalPreparation, body["preparation_id"])
    assert preparation.self_evolution_token_encrypted is None

    run_body = _create(client, body["preparation_id"], "idem-swe-off")
    run = session.get(EvalRun, run_body["id"])
    assert run.self_evolution_enabled is False
    assert run.self_evolution_base_url is None
    assert run.self_evolution_workspace_id is None
    assert run.self_evolution_token_encrypted is None


def test_prepare_response_never_contains_plaintext_token(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, self_evolution=CONFIG),
    )
    assert response.status_code == 200, response.text
    assert TOKEN not in response.text
    assert TOKEN not in response.json()["summary"].__str__()

"""M5-02 model credential console API.

Covers the console surface the model-credentials page depends on:

- the list/detail payload carries the credential mask, the recent-use summary
  and pricing, and never the ciphertext or plaintext key;
- admins create configs with a new or an existing credential, rotate and
  revoke credentials, and the list immediately reflects the new state;
- a viewer can read but every write is refused by the API, not just hidden in
  the UI;
- connectivity-test failures keep a stable error code and identify the form
  field that must change;
- creating a config with a revoked credential is rejected, so preflight's
  ``credential_unavailable`` blocker cannot be bypassed from the page.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from app.clients import get_provider_client
from app.config import get_settings
from app.defaults import DEFAULT_TENANT_ID
from app.main import app
from app.models import ModelUsage, Tenant, Workspace
from app.secrets import reset_secret_box_cache
from app.services import identity
from conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session


class FakeProvider:
    def __init__(self) -> None:
        self.status = 200
        self.body: dict = {"data": [{"id": "gpt-test"}]}
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def model_env(configure_settings):
    configure_settings(SECRET_BOX_KEY=Fernet.generate_key().decode())
    reset_secret_box_cache()
    yield get_settings()
    reset_secret_box_cache()


@pytest.fixture
def provider(model_env):
    fake = FakeProvider()
    transport = httpx.MockTransport(fake.handler)

    async def override_provider():
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    app.dependency_overrides[get_provider_client] = override_provider
    yield fake
    app.dependency_overrides.pop(get_provider_client, None)


def _create_config(client, *, secret: str = "sk-live-1234567890", **overrides) -> dict:
    body = {
        "provider": "openai",
        "display_name": "Test model",
        "base_url": "https://provider.test/v1",
        "model_name": "gpt-test",
        "secret": secret,
        "pricing": {"input_per_1m": 1000, "output_per_1m": 2000},
    }
    body.update(overrides)
    response = client.post("/api/v1/model-configs", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _login_viewer(client, session: Session) -> None:
    tenant = session.scalar(select(Tenant).where(Tenant.slug == "default"))
    assert tenant is not None
    workspace = session.scalar(select(Workspace).where(Workspace.tenant_id == tenant.id))
    assert workspace is not None
    identity.upsert_member(
        session,
        email=TEST_VIEWER_EMAIL,
        password=TEST_VIEWER_PASSWORD,
        tenant=tenant,
        workspace=workspace,
        role=identity.ROLE_VIEWER,
        is_default=True,
    )
    session.commit()
    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": TEST_VIEWER_EMAIL, "password": TEST_VIEWER_PASSWORD},
    )
    assert login.status_code == 200, login.text


def _add_usage(engine, config_id: str, *, cost: str = "0.020000") -> None:
    with Session(engine) as session:
        session.add(
            ModelUsage(
                tenant_id=str(DEFAULT_TENANT_ID),
                model_config_id=config_id,
                status="succeeded",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                cost_usd=Decimal(cost),
                created_at=datetime.now(UTC),
            )
        )
        session.commit()


def test_list_returns_mask_pricing_and_recent_use_without_secret(
    client, model_env, engine
) -> None:
    config = _create_config(client, secret="sk-live-abcdefgh")
    _add_usage(engine, config["id"])

    response = client.get("/api/v1/model-configs")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "sk-live-abcdefgh" not in response.text
    assert "ciphertext" not in response.text
    assert "key_version" not in response.text

    item = next(row for row in body["items"] if row["id"] == config["id"])
    assert item["credential"]["secret_mask"] == "****efgh"
    assert item["credential"]["revoked_at"] is None
    assert item["pricing"] == {"input_per_1m": 1000, "output_per_1m": 2000}
    assert item["usage"] == {
        "requests": 1,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost_usd": "0.020000",
    }
    assert item["last_used_at"] is not None

    detail = client.get(f"/api/v1/model-configs/{config['id']}")
    assert detail.status_code == 200
    assert detail.json()["credential"]["secret_mask"] == "****efgh"


def test_create_binds_existing_credential_and_list_sees_it_immediately(
    client, model_env
) -> None:
    created_credential = client.post(
        "/api/v1/credentials",
        json={"provider": "minimax", "secret": "sk-existing-9876", "kind": "model"},
    )
    assert created_credential.status_code == 201
    credential = created_credential.json()

    config = _create_config(
        client,
        secret=None,
        credential_id=credential["id"],
        provider="minimax",
        api_style="anthropic",
    )
    assert config["credential"]["id"] == credential["id"]
    assert config["credential"]["secret_mask"] == "****9876"

    body = client.get("/api/v1/model-configs").json()
    assert any(row["id"] == config["id"] for row in body["items"])

    # Only model credentials are offered by the model page's credential picker.
    model_only = client.get("/api/v1/credentials?kind=model").json()["items"]
    assert [row["id"] for row in model_only] == [credential["id"]]


def test_credentials_kind_filter_excludes_resource_credentials(client, model_env) -> None:
    model = client.post(
        "/api/v1/credentials",
        json={"provider": "openai", "secret": "sk-model-1111", "kind": "model"},
    ).json()
    resource = client.post(
        "/api/v1/credentials",
        json={"provider": "aliyun", "secret": "ak-resource-2222", "kind": "resource"},
    ).json()

    assert [row["id"] for row in client.get("/api/v1/credentials?kind=model").json()["items"]] == [
        model["id"]
    ]
    assert [
        row["id"] for row in client.get("/api/v1/credentials?kind=resource").json()["items"]
    ] == [resource["id"]]
    assert len(client.get("/api/v1/credentials").json()["items"]) == 2


def test_rotate_and_revoke_are_visible_on_the_config_list(client, model_env) -> None:
    config = _create_config(client, secret="sk-before-0000")
    credential_id = config["credential"]["id"]

    rotated = client.post(
        f"/api/v1/credentials/{credential_id}/rotate",
        json={"secret": "sk-after-9999"},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["secret_mask"] == "****9999"

    item = client.get(f"/api/v1/model-configs/{config['id']}").json()
    assert item["credential"]["secret_mask"] == "****9999"
    assert item["credential"]["revoked_at"] is None

    revoked = client.delete(f"/api/v1/credentials/{credential_id}")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    item = client.get(f"/api/v1/model-configs/{config['id']}").json()
    assert item["credential"]["revoked_at"] is not None

    rejected = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "openai",
            "display_name": "Revoked binding",
            "base_url": "https://provider.test/v1",
            "model_name": "gpt-test",
            "credential_id": credential_id,
        },
    )
    assert rejected.status_code == 422, rejected.text
    error = rejected.json()["detail"]["error"]
    assert error["code"] == "credential_revoked"
    assert "credential_id" in error["fields"]


def test_viewer_reads_but_cannot_write_models(client, model_env, session: Session) -> None:
    config = _create_config(client)
    credential_id = config["credential"]["id"]

    _login_viewer(client, session)

    assert client.get("/api/v1/model-configs").status_code == 200
    assert client.get(f"/api/v1/model-configs/{config['id']}").status_code == 200
    assert client.get("/api/v1/credentials?kind=model").status_code == 200

    writes = [
        client.post(
            "/api/v1/model-configs",
            json={
                "provider": "openai",
                "display_name": "Nope",
                "base_url": "https://provider.test/v1",
                "model_name": "gpt-test",
                "secret": "sk-nope-1234",
            },
        ),
        client.patch(f"/api/v1/model-configs/{config['id']}", json={"display_name": "Nope"}),
        client.post(f"/api/v1/model-configs/{config['id']}/test"),
        client.post(
            "/api/v1/credentials", json={"provider": "openai", "secret": "sk-nope-1234"}
        ),
        client.post(
            f"/api/v1/credentials/{credential_id}/rotate", json={"secret": "sk-nope-1234"}
        ),
        client.delete(f"/api/v1/credentials/{credential_id}"),
    ]
    for response in writes:
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"]["code"] == "forbidden"

    # The viewer's attempts did not change the credential state.
    assert client.get(f"/api/v1/model-configs/{config['id']}").json()["credential"][
        "revoked_at"
    ] is None


def test_connectivity_failure_identifies_the_field(client, model_env, provider) -> None:
    config = _create_config(client)

    provider.status = 401
    auth_failure = client.post(f"/api/v1/model-configs/{config['id']}/test")
    assert auth_failure.status_code == 502, auth_failure.text
    error = auth_failure.json()["detail"]["error"]
    assert error["code"] == "authentication_failed"
    assert "secret" in error["fields"]

    provider.status = 500
    unavailable = client.post(f"/api/v1/model-configs/{config['id']}/test")
    assert unavailable.status_code == 503, unavailable.text
    error = unavailable.json()["detail"]["error"]
    assert error["code"] == "provider_unavailable"
    assert "base_url" in error["fields"]

    provider.status = 200
    ok = client.post(f"/api/v1/model-configs/{config['id']}/test")
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"
    assert provider.calls[-1].headers["authorization"] == "Bearer sk-live-1234567890"


def test_patch_validation_returns_field_level_errors(client, model_env) -> None:
    config = _create_config(client)

    updated = client.patch(
        f"/api/v1/model-configs/{config['id']}",
        json={
            "display_name": "Renamed",
            "api_style": "anthropic",
            "pricing": {"input_per_1m": 3, "output_per_1m": 4},
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["display_name"] == "Renamed"
    assert updated.json()["api_style"] == "anthropic"
    assert updated.json()["pricing"] == {"input_per_1m": 3, "output_per_1m": 4}

    bad_url = client.patch(
        f"/api/v1/model-configs/{config['id']}", json={"base_url": "provider.test/v1"}
    )
    assert bad_url.status_code == 422
    assert "base_url" in bad_url.json()["detail"]["error"]["fields"]

    bad_style = client.patch(
        f"/api/v1/model-configs/{config['id']}", json={"api_style": "grpc"}
    )
    assert bad_style.status_code == 422
    assert "api_style" in bad_style.json()["detail"]["error"]["fields"]


def test_missing_credential_returns_field_level_error(client, model_env) -> None:
    response = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "openai",
            "display_name": "No key",
            "base_url": "https://provider.test/v1",
            "model_name": "gpt-test",
        },
    )
    assert response.status_code == 422
    error = response.json()["detail"]["error"]
    assert error["code"] == "credential_required"
    assert "credential_id" in error["fields"]
    assert "secret" in error["fields"]


def test_responses_never_include_plaintext_or_ciphertext(client, model_env) -> None:
    """The page renders masks only; this asserts the API contract it relies on."""

    config = _create_config(client, secret="sk-plaintext-must-not-leak")
    list_text = client.get("/api/v1/model-configs").text
    detail_text = client.get(f"/api/v1/model-configs/{config['id']}").text
    credentials_text = client.get("/api/v1/credentials?kind=model").text

    for text in (list_text, detail_text, credentials_text):
        assert "sk-plaintext-must-not-leak" not in text
        assert "ciphertext" not in text
        assert "key_version" not in text

    # The mask is the only credential material the console can display.
    assert "****leak" in json.dumps(client.get("/api/v1/model-configs").json())


def test_temporary_configs_stay_out_of_the_public_catalog(client, model_env) -> None:
    """Design 05 §5: a temporary config belongs to one run, not the catalog."""

    temporary = _create_config(
        client,
        display_name="Temporary model",
        is_temporary=True,
        secret="sk-temp-4321",
    )
    listed = client.get("/api/v1/model-configs").json()["items"]
    assert temporary["id"] not in [row["id"] for row in listed]

    explicit = client.get("/api/v1/model-configs?include_temporary=true").json()["items"]
    assert temporary["id"] in [row["id"] for row in explicit]

    detail = client.get(f"/api/v1/model-configs/{temporary['id']}")
    assert detail.status_code == 200
    assert detail.json()["is_temporary"] is True

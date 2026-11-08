from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
import pytest
from app.api.model_gateway import get_session_factory
from app.clients import get_gateway_client, get_provider_client
from app.config import get_settings
from app.defaults import DEFAULT_TENANT_ID
from app.main import app
from app.model_gateway.tokens import GatewayClaims, issue_token
from app.models import ModelUsage
from app.secrets import reset_secret_box_cache
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.status = 200
        self.body: dict = {
            "id": "chatcmpl-test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        self.stream_body: bytes | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.stream_body is not None:
            return httpx.Response(
                200,
                content=self.stream_body,
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def model_env(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "secret_box_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "gateway_token_secret", "test-gateway-secret")
    reset_secret_box_cache()
    yield settings
    reset_secret_box_cache()


@pytest.fixture
def provider(model_env):
    fake = FakeProvider()
    transport = httpx.MockTransport(fake.handler)

    async def override_gateway():
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    async def override_provider():
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    app.dependency_overrides[get_gateway_client] = override_gateway
    app.dependency_overrides[get_provider_client] = override_provider
    yield fake
    app.dependency_overrides.pop(get_gateway_client, None)
    app.dependency_overrides.pop(get_provider_client, None)


@pytest.fixture
def usage_factory(engine):
    factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, class_=Session
    )
    app.dependency_overrides[get_session_factory] = lambda: factory
    yield factory
    app.dependency_overrides.pop(get_session_factory, None)


def _create_config(client, *, secret: str = "sk-test-1234567890", pricing=None) -> dict:
    response = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "openai",
            "display_name": "Test model",
            "base_url": "https://provider.test/v1",
            "model_name": "gpt-test",
            "secret": secret,
            "pricing": pricing or {"input_per_1m": 1_000, "output_per_1m": 2_000},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _mint(config_id: str, **overrides) -> str:
    values = {
        "tenant_id": str(DEFAULT_TENANT_ID),
        "model_config_id": config_id,
        "allowed_models": ("gpt-test",),
        "exp": int(time.time()) + 300,
        "eval_run_id": "run-1",
        "case_run_id": "case-1",
        "attempt": 1,
        "budget_usd": None,
    }
    values.update(overrides)
    return issue_token(GatewayClaims(**values), "test-gateway-secret")


def _usage_rows(engine) -> list[ModelUsage]:
    with Session(engine) as session:
        return list(session.scalars(select(ModelUsage)))


def test_credentials_are_never_returned_in_plaintext(client) -> None:
    created = client.post(
        "/api/v1/credentials",
        json={"provider": "openai", "secret": "sk-secret-1234567890"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["secret_mask"] == "****7890"
    assert "ciphertext" not in body
    assert "sk-secret" not in json.dumps(body)

    listed = client.get("/api/v1/credentials")
    assert listed.status_code == 200
    assert "sk-secret" not in listed.text

    rotated = client.post(
        f"/api/v1/credentials/{body['id']}/rotate",
        json={"secret": "sk-rotated-abcdefgh"},
    )
    assert rotated.status_code == 200
    assert rotated.json()["secret_mask"] == "****efgh"

    revoked = client.delete(f"/api/v1/credentials/{body['id']}")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None


def test_model_config_requires_exactly_one_credential(client) -> None:
    missing = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "openai",
            "display_name": "No credential",
            "base_url": "https://provider.test/v1",
            "model_name": "gpt-test",
        },
    )
    assert missing.status_code == 422
    both = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "openai",
            "display_name": "Both",
            "base_url": "https://provider.test/v1",
            "model_name": "gpt-test",
            "secret": "sk-both",
            "credential_id": "cred-1",
        },
    )
    assert both.status_code == 422


def test_model_config_connectivity_test(client, provider) -> None:
    config = _create_config(client)
    response = client.post(f"/api/v1/model-configs/{config['id']}/test")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert provider.calls[0].headers["authorization"] == "Bearer sk-test-1234567890"


def test_gateway_forwards_and_records_usage(client, provider, engine, usage_factory) -> None:
    config = _create_config(client)
    token = _mint(config["id"])
    response = client.post(
        "/model-gateway/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "chatcmpl-test"
    assert provider.calls[0].headers["authorization"] == "Bearer sk-test-1234567890"

    rows = _usage_rows(engine)
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
    assert rows[0].prompt_tokens == 10
    assert rows[0].completion_tokens == 5
    assert rows[0].cost_usd == Decimal("0.020000")
    assert rows[0].eval_run_id == "run-1"
    assert rows[0].case_run_id == "case-1"


def test_gateway_rejects_missing_or_expired_tokens(client, model_env) -> None:
    missing = client.post(
        "/model-gateway/v1/chat/completions",
        json={"model": "gpt-test", "messages": []},
    )
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "missing_token"

    config = _create_config(client)
    expired = _mint(config["id"], exp=int(time.time()) - 1)
    response = client.post(
        "/model-gateway/v1/chat/completions",
        headers={"Authorization": f"Bearer {expired}"},
        json={"model": "gpt-test", "messages": []},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


def test_gateway_rejects_model_outside_token_scope(
    client, provider, engine, usage_factory
) -> None:
    config = _create_config(client)
    token = _mint(config["id"], allowed_models=("another-model",))
    response = client.post(
        "/model-gateway/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-test", "messages": []},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_forbidden"
    assert provider.calls == []
    assert _usage_rows(engine) == []


def test_gateway_pauses_when_budget_reached(client, provider, engine, usage_factory) -> None:
    config = _create_config(client)
    with Session(engine) as session:
        session.add(
            ModelUsage(
                tenant_id=str(DEFAULT_TENANT_ID),
                model_config_id=config["id"],
                eval_run_id="run-1",
                case_run_id="case-1",
                attempt=1,
                status="succeeded",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost_usd=Decimal("0.500000"),
            )
        )
        session.commit()

    token = _mint(config["id"], budget_usd=0.01)
    response = client.post(
        "/model-gateway/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-test", "messages": []},
    )
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "budget_paused"
    assert provider.calls == []
    rows = _usage_rows(engine)
    assert any(row.status == "budget_paused" for row in rows)


def test_gateway_normalizes_upstream_rate_limit(
    client, provider, engine, usage_factory
) -> None:
    config = _create_config(client)
    provider.status = 429
    token = _mint(config["id"])
    response = client.post(
        "/model-gateway/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-test", "messages": []},
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert _usage_rows(engine)[0].status == "failed"


def test_gateway_streams_and_records_usage(client, provider, engine, usage_factory) -> None:
    config = _create_config(client)
    provider.stream_body = (
        b'data: {"id":"chunk","usage":{"prompt_tokens":7,"completion_tokens":3}}\n\n'
        b"data: [DONE]\n\n"
    )
    token = _mint(config["id"])
    response = client.post(
        "/model-gateway/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-test", "messages": [], "stream": True},
    )
    assert response.status_code == 200
    assert "[DONE]" in response.text
    rows = _usage_rows(engine)
    assert rows[0].status == "succeeded"
    assert rows[0].prompt_tokens == 7
    assert rows[0].completion_tokens == 3


def test_revoked_credential_blocks_gateway(client, provider, engine, usage_factory) -> None:
    config = _create_config(client)
    client.delete(f"/api/v1/credentials/{config['credential_id']}")
    token = _mint(config["id"])
    response = client.post(
        "/model-gateway/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-test", "messages": []},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "credential_revoked"
    assert provider.calls == []


def _create_anthropic_config(client, *, secret: str = "sk-ant-test-1234567890") -> dict:
    response = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "minimax",
            "display_name": "MiniMax via Anthropic",
            "base_url": "https://api.minimax.cn/anthropic",
            "model_name": "MiniMax-M3",
            "api_style": "anthropic",
            "secret": secret,
            "pricing": {"input_per_1m": 5_000, "output_per_1m": 20_000},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_anthropic_gateway_forwards_and_records_usage(
    client, provider, engine, usage_factory
) -> None:
    config = _create_anthropic_config(client)
    provider.body = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 20, "output_tokens": 5},
    }
    claims = GatewayClaims(
        tenant_id=str(DEFAULT_TENANT_ID),
        model_config_id=config["id"],
        allowed_models=("MiniMax-M3",),
        exp=int(time.time()) + 300,
        eval_run_id="run-1",
        case_run_id="case-1",
        attempt=1,
    )
    token = issue_token(claims, "test-gateway-secret")
    response = client.post(
        "/model-gateway/anthropic/v1/messages",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
        },
        json={"model": "MiniMax-M3", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "msg_test"
    assert provider.calls[0].headers["x-api-key"] == "sk-ant-test-1234567890"
    assert provider.calls[0].url.path.endswith("/anthropic/v1/messages")

    rows = _usage_rows(engine)
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 20
    assert rows[0].completion_tokens == 5
    assert rows[0].cost_usd == Decimal("0.200000")
    assert rows[0].status == "succeeded"


def test_anthropic_gateway_streams_usage(client, provider, engine, usage_factory) -> None:
    config = _create_anthropic_config(client)
    provider.stream_body = (
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":11,"output_tokens":1}}}\n\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":7}}\n\n'
        b'data: {"type":"message_stop"}\n\n'
    )
    claims = GatewayClaims(
        tenant_id=str(DEFAULT_TENANT_ID),
        model_config_id=config["id"],
        allowed_models=("MiniMax-M3",),
        exp=int(time.time()) + 300,
        eval_run_id="run-1",
        case_run_id="case-1",
        attempt=1,
    )
    token = issue_token(claims, "test-gateway-secret")
    response = client.post(
        "/model-gateway/anthropic/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "MiniMax-M3", "stream": True, "messages": []},
    )
    assert response.status_code == 200
    rows = _usage_rows(engine)
    assert rows[0].prompt_tokens == 11
    assert rows[0].completion_tokens == 7
    assert rows[0].status == "succeeded"

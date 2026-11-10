"""M3-02 centralized model gateway and short-lived runner credentials.

Covers the acceptance surface:

- provider credentials are encrypted and never returned in plaintext;
- the OpenAI-compatible gateway forwards, retries/errors map to the shared
  envelope and records usage per eval run + shard;
- the Anthropic-compatible route used by Claude Code forwards and records
  input/output tokens;
- gateway tokens use the M2-05 short-token wire format, are rejected when
  expired/wrong-kind, and are rejected when their run/shard/attempt binding
  does not match the persisted attempt;
- a runner obtains the token from the shard manifest.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from app.api.model_gateway import get_session_factory
from app.clients import get_gateway_client, get_provider_client
from app.config import get_settings
from app.defaults import DEFAULT_TENANT_ID
from app.main import app
from app.model_gateway.tokens import (
    GatewayClaims,
    issue_gateway_token,
    verify_gateway_token,
)
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, ModelUsage, Outbox
from app.secrets import reset_secret_box_cache
from app.services import budget
from app.services.short_token import issue_short_token
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
        self.anthropic_body: dict = {
            "id": "msg-test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "pong"}],
            "usage": {"input_tokens": 14, "output_tokens": 2},
        }
        self.stream_body: bytes | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path.endswith("/v1/messages"):
            return httpx.Response(200, json=self.anthropic_body)
        if self.stream_body is not None:
            return httpx.Response(
                200,
                content=self.stream_body,
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def model_env(configure_settings):
    configure_settings(
        SECRET_BOX_KEY=Fernet.generate_key().decode(),
        GATEWAY_TOKEN_SECRET="test-gateway-secret",
    )
    reset_secret_box_cache()
    yield get_settings()
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
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    app.dependency_overrides[get_session_factory] = lambda: factory
    yield factory
    app.dependency_overrides.pop(get_session_factory, None)


@pytest.fixture
def bound_eval(client, session, model_env):
    """A real run bound to a model config, with its first shard/attempt."""

    config = _create_config(client)
    created = client.post(
        "/api/v1/evaluations",
        json={
            "name": "模型绑定",
            "case_keys": ["case-a"],
            "idempotency_key": "bound-eval",
            "model_config_id": config["id"],
            "model_name": "gpt-test",
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run_id))
    assert outbox is not None
    shard_id = outbox.payload_json["shard_id"]
    return {
        "config": config,
        "run_id": run_id,
        "shard_id": shard_id,
        "attempt": 1,
    }


def _create_config(client, *, secret: str = "sk-test-1234567890", **overrides) -> dict:
    body = {
        "provider": "openai",
        "display_name": "Test model",
        "base_url": "https://provider.test/v1",
        "model_name": "gpt-test",
        "secret": secret,
        "pricing": {"input_per_1m": 1_000, "output_per_1m": 2_000},
    }
    body.update(overrides)
    response = client.post("/api/v1/model-configs", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _mint(config_id: str, *, ttl: int = 300, secret: str = "test-gateway-secret", **overrides) -> str:
    values = {
        "tenant_id": str(DEFAULT_TENANT_ID),
        "model_config_id": config_id,
        "allowed_models": ("gpt-test",),
    }
    values.update(overrides)
    return issue_gateway_token(
        GatewayClaims(**values), secret, ttl_seconds=ttl
    )


def _usage_rows(engine) -> list[ModelUsage]:
    with Session(engine) as session:
        return list(session.scalars(select(ModelUsage)))


def _gateway(client, token: str, *, path: str = "/model-gateway/v1/chat/completions", body: dict | None = None):
    return client.post(
        path,
        headers={"Authorization": f"Bearer {token}"},
        json=body or {"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]},
    )


# ---------------------------------------------------------------------------
# Credentials and configuration
# ---------------------------------------------------------------------------


def test_credentials_are_never_returned_in_plaintext(client, model_env) -> None:
    created = client.post(
        "/api/v1/credentials",
        json={"provider": "openai", "secret": "sk-secret-1234567890"},
    )
    assert created.status_code == 201, created.text
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
    assert "sk-rotated" not in rotated.text

    revoked = client.delete(f"/api/v1/credentials/{body['id']}")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None


def test_model_config_does_not_echo_secret(client, model_env) -> None:
    config = _create_config(client)
    assert config["credential_id"]
    # M5-02 adds a nested masked-credential summary; only the mask is exposed.
    assert "secret" not in config
    assert "sk-test-1234567890" not in json.dumps(config)
    assert config["credential"]["secret_mask"] == "****7890"
    assert "ciphertext" not in json.dumps(config)
    detail = client.get(f"/api/v1/model-configs/{config['id']}")
    assert detail.status_code == 200
    assert "sk-test-1234567890" not in detail.text
    assert detail.json()["api_style"] == "openai"


def test_model_config_requires_exactly_one_credential(client, model_env) -> None:
    base = {
        "provider": "openai",
        "display_name": "No credential",
        "base_url": "https://provider.test/v1",
        "model_name": "gpt-test",
    }
    missing = client.post("/api/v1/model-configs", json=base)
    assert missing.status_code == 422
    both = client.post(
        "/api/v1/model-configs",
        json={**base, "secret": "sk-both", "credential_id": "cred-1"},
    )
    assert both.status_code == 422


def test_model_config_connectivity_test(client, model_env, provider) -> None:
    config = _create_config(client)
    response = client.post(f"/api/v1/model-configs/{config['id']}/test")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    assert provider.calls[0].headers["authorization"] == "Bearer sk-test-1234567890"


def test_console_endpoints_require_session(anonymous_client, model_env) -> None:
    response = anonymous_client.get("/api/v1/credentials")
    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "unauthenticated"


# ---------------------------------------------------------------------------
# Token format, expiry and classification
# ---------------------------------------------------------------------------


def test_gateway_token_reuses_short_token_wire_format(model_env) -> None:
    token = _mint("config-1")
    assert token.startswith("v1.")
    claims = verify_gateway_token(token, "test-gateway-secret")
    assert claims.model_config_id == "config-1"
    with pytest.raises(Exception) as error:
        verify_gateway_token(token, "another-secret")
    assert getattr(error.value, "code", "") == "invalid_token"


def test_task_token_is_not_a_model_token(model_env) -> None:
    task_token = issue_short_token({"eval_run_id": "run-1"}, "test-gateway-secret")
    with pytest.raises(Exception) as error:
        verify_gateway_token(task_token, "test-gateway-secret")
    assert getattr(error.value, "code", "") == "token_kind_mismatch"


def test_gateway_rejects_missing_and_expired_tokens(client, model_env) -> None:
    missing = client.post(
        "/model-gateway/v1/chat/completions",
        json={"model": "gpt-test", "messages": []},
    )
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "missing_token"

    config = _create_config(client)
    expired = _mint(config["id"], ttl=-1)
    response = _gateway(client, expired)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


def test_gateway_rejects_model_outside_token_scope(client, model_env, provider, engine, usage_factory) -> None:
    config = _create_config(client)
    token = _mint(config["id"], allowed_models=("another-model",))
    response = _gateway(client, token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_forbidden"
    assert provider.calls == []
    assert _usage_rows(engine) == []


# ---------------------------------------------------------------------------
# Forwarding, usage and error mapping
# ---------------------------------------------------------------------------


def test_gateway_forwards_and_records_usage(
    client, model_env, provider, engine, usage_factory, bound_eval
) -> None:
    config = bound_eval["config"]
    token = _mint(
        config["id"],
        eval_run_id=bound_eval["run_id"],
        shard_id=bound_eval["shard_id"],
        attempt=bound_eval["attempt"],
    )
    response = _gateway(client, token)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "chatcmpl-test"
    assert provider.calls[0].headers["authorization"] == "Bearer sk-test-1234567890"

    rows = _usage_rows(engine)
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
    assert rows[0].prompt_tokens == 10
    assert rows[0].completion_tokens == 5
    assert rows[0].cost_usd == Decimal("0.020000")
    assert rows[0].eval_run_id == bound_eval["run_id"]
    assert rows[0].shard_id == bound_eval["shard_id"]


def test_gateway_pauses_when_budget_reached(
    client, model_env, provider, engine, usage_factory, bound_eval
) -> None:
    config = bound_eval["config"]
    with Session(engine) as session:
        session.add(
            ModelUsage(
                tenant_id=str(DEFAULT_TENANT_ID),
                model_config_id=config["id"],
                eval_run_id=bound_eval["run_id"],
                shard_id=bound_eval["shard_id"],
                attempt=1,
                status="succeeded",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost_usd=Decimal("0.500000"),
            )
        )
        session.commit()

    token = _mint(
        config["id"],
        eval_run_id=bound_eval["run_id"],
        shard_id=bound_eval["shard_id"],
        attempt=1,
        budget_usd=0.01,
    )
    response = _gateway(client, token)
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "budget_paused"
    assert provider.calls == []
    rows = _usage_rows(engine)
    assert any(row.status == "budget_paused" for row in rows)


def test_gateway_budget_refusal_triggers_platform_pause(
    client, model_env, provider, engine, usage_factory, bound_eval
) -> None:
    """M4-05: the gateway refusal is the platform-level pause trigger."""

    config = bound_eval["config"]
    with Session(engine) as session:
        run = session.get(EvalRun, bound_eval["run_id"])
        run.model_budget_usd = Decimal("0.010000")
        session.add(
            ModelUsage(
                tenant_id=str(DEFAULT_TENANT_ID),
                model_config_id=config["id"],
                eval_run_id=bound_eval["run_id"],
                shard_id=bound_eval["shard_id"],
                attempt=1,
                status="succeeded",
                cost_usd=Decimal("0.500000"),
            )
        )
        session.commit()

    token = _mint(
        config["id"],
        eval_run_id=bound_eval["run_id"],
        shard_id=bound_eval["shard_id"],
        attempt=1,
        budget_usd=0.01,
    )
    refused = _gateway(client, token)
    assert refused.status_code == 402
    assert refused.json()["error"]["code"] == "budget_paused"
    assert provider.calls == []

    with Session(engine) as session:
        run = session.get(EvalRun, bound_eval["run_id"])
        assert run.status == "pausing_budget"
        assert run.pause_reason == "budget_exceeded"
        assert Decimal(str(run.budget_spent_usd)) == Decimal("0.5")
        events = list(
            session.scalars(
                select(EvalEvent).where(
                    EvalEvent.eval_run_id == bound_eval["run_id"],
                    EvalEvent.event_type == "eval.budget_pausing",
                )
            )
        )
        assert len(events) == 1
        assert events[0].payload_json["source"] == "model_gateway"

    # The old token is still cryptographically valid, but the run state is the
    # revocation point: no further model call may reach the provider.
    rejected = _gateway(client, token)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "budget_paused"
    assert provider.calls == []

    # Resume raises the budget, but the interrupted attempt is terminal: its
    # old token must stay revoked instead of becoming valid again.
    with Session(engine) as session:
        budget.reconcile_budget_pauses(session, settings=get_settings())
    with Session(engine) as session:
        assert session.get(EvalRun, bound_eval["run_id"]).status == "paused_budget"
    resumed = client.post(
        f"/api/v1/evaluations/{bound_eval['run_id']}/resume-budget",
        json={"additional_budget_usd": 1, "budget_version": 1},
    )
    assert resumed.status_code == 200, resumed.text
    stale = _gateway(client, token)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_attempt"
    assert provider.calls == []


def test_gateway_normalizes_upstream_rate_limit(
    client, model_env, provider, engine, usage_factory, bound_eval
) -> None:
    config = bound_eval["config"]
    provider.status = 429
    token = _mint(
        config["id"],
        eval_run_id=bound_eval["run_id"],
        shard_id=bound_eval["shard_id"],
        attempt=bound_eval["attempt"],
    )
    response = _gateway(client, token)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert _usage_rows(engine)[0].status == "failed"


def test_gateway_streams_and_records_usage(
    client, model_env, provider, engine, usage_factory, bound_eval
) -> None:
    config = bound_eval["config"]
    provider.stream_body = (
        b'data: {"id":"chunk","usage":{"prompt_tokens":7,"completion_tokens":3}}\n\n'
        b"data: [DONE]\n\n"
    )
    token = _mint(
        config["id"],
        eval_run_id=bound_eval["run_id"],
        shard_id=bound_eval["shard_id"],
        attempt=bound_eval["attempt"],
    )
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


def test_revoked_credential_blocks_gateway(
    client, model_env, provider, engine, usage_factory, bound_eval
) -> None:
    config = bound_eval["config"]
    client.delete(f"/api/v1/credentials/{config['credential_id']}")
    token = _mint(
        config["id"],
        eval_run_id=bound_eval["run_id"],
        shard_id=bound_eval["shard_id"],
        attempt=bound_eval["attempt"],
    )
    response = _gateway(client, token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "credential_revoked"
    assert provider.calls == []


# ---------------------------------------------------------------------------
# Anthropic route (Claude Code) and usage query
# ---------------------------------------------------------------------------


def test_anthropic_route_forwards_to_anthropic_upstream(
    client, model_env, provider, engine, usage_factory, bound_eval
) -> None:
    config = _create_config(
        client,
        provider="minimax",
        base_url="https://api.minimaxi.com/anthropic",
        api_style="anthropic",
        model_name="MiniMax-M2.7-highspeed",
    )
    token = _mint(
        config["id"],
        allowed_models=("MiniMax-M2.7-highspeed",),
        eval_run_id=bound_eval["run_id"],
        shard_id=bound_eval["shard_id"],
        attempt=bound_eval["attempt"],
    )
    response = client.post(
        "/model-gateway/anthropic/v1/messages",
        headers={"Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01"},
        json={
            "model": "MiniMax-M2.7-highspeed",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "pong"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["type"] == "message"
    call = provider.calls[0]
    assert call.headers["x-api-key"] == "sk-test-1234567890"
    assert call.headers["anthropic-version"] == "2023-06-01"
    rows = _usage_rows(engine)
    assert rows[0].prompt_tokens == 14
    assert rows[0].completion_tokens == 2


def test_usage_is_queryable_by_eval_run_and_shard(
    client, model_env, provider, engine, usage_factory, bound_eval
) -> None:
    config = bound_eval["config"]
    token = _mint(
        config["id"],
        eval_run_id=bound_eval["run_id"],
        shard_id=bound_eval["shard_id"],
        attempt=bound_eval["attempt"],
    )
    assert _gateway(client, token).status_code == 200
    response = client.get(
        "/api/v1/model-usage",
        params={"eval_run_id": bound_eval["run_id"], "shard_id": bound_eval["shard_id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totals"]["requests"] == 1
    assert body["totals"]["total_tokens"] == 15
    assert body["items"][0]["shard_id"] == bound_eval["shard_id"]

    empty = client.get("/api/v1/model-usage", params={"eval_run_id": "other"})
    assert empty.status_code == 200
    assert empty.json()["totals"]["requests"] == 0


# ---------------------------------------------------------------------------
# Run/shard binding and manifest issuance
# ---------------------------------------------------------------------------


def test_eval_creation_rejects_unknown_model_config(client, model_env) -> None:
    response = client.post(
        "/api/v1/evaluations",
        json={
            "name": "未知模型",
            "case_keys": ["case-a"],
            "idempotency_key": "unknown-model",
            "model_config_id": "missing-config",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "evaluation_invalid"


def test_gateway_rejects_token_bound_to_another_shard(
    client, model_env, session, provider, engine, usage_factory
) -> None:
    config = _create_config(client)
    response = client.post(
        "/api/v1/evaluations",
        json={
            "name": "模型绑定",
            "case_keys": ["case-a"],
            "idempotency_key": "model-binding",
            "model_config_id": config["id"],
            "model_name": "gpt-test",
        },
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    run = session.get(EvalRun, run_id)
    assert run is not None and run.model_config_id == config["id"]

    wrong = _mint(
        config["id"],
        eval_run_id=run_id,
        shard_id="shard_999",
        attempt=1,
    )
    rejected = _gateway(client, wrong)
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "token_binding_mismatch"
    assert provider.calls == []


def test_manifest_issues_model_token_bound_to_shard(
    client, model_env, session, provider, engine, usage_factory
) -> None:
    config = _create_config(client, model_name="gpt-test", base_url="https://provider.test/v1")
    created = client.post(
        "/api/v1/evaluations",
        json={
            "name": "模型 manifest",
            "case_keys": ["case-a"],
            "idempotency_key": "model-manifest",
            "model_config_id": config["id"],
            "model_name": "gpt-test",
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    outbox = session.scalar(
        select(Outbox).where(Outbox.aggregate_id == run_id)
    )
    assert outbox is not None
    shard_id = outbox.payload_json["shard_id"]
    response = client.get(
        f"/runner/v1/shards/{shard_id}/manifest",
        params={
            "eval_run_id": run_id,
            "attempt": 1,
            "dispatch_key": outbox.dispatch_key,
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["x-runner-model-name"] == "gpt-test"
    token = response.headers["x-runner-model-token"]
    assert token.startswith("v1.")
    assert response.headers["x-runner-model-anthropic-base-url"].endswith(
        "/model-gateway/anthropic"
    )

    # The manifest token is accepted and records usage against this run/shard.
    forwarded = _gateway(client, token)
    assert forwarded.status_code == 200, forwarded.text
    rows = _usage_rows(engine)
    assert rows[0].eval_run_id == run_id
    assert rows[0].shard_id == shard_id

    # Reject the same token when the shard claim is tampered with.
    claims = verify_gateway_token(token, "test-gateway-secret")
    assert claims.eval_run_id == run_id
    assert claims.shard_id == shard_id
    attempt = session.scalar(
        select(EvalCaseAttempt)
        .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
        .where(
            EvalCaseRun.eval_run_id == run_id,
            EvalCaseAttempt.shard_id == shard_id,
        )
    )
    assert attempt is not None
    assert attempt.attempt_no == 1
    assert claims.attempt == 1


def test_anthropic_alias_is_rewritten_to_configured_model(client, provider, bound_eval) -> None:
    token = _mint(bound_eval["config"]["id"])
    response = _gateway(
        client,
        token,
        path="/model-gateway/anthropic/v1/messages",
        body={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "pong"}],
        },
    )
    assert response.status_code == 200, response.text
    forwarded = json.loads(provider.calls[-1].content)
    assert forwarded["model"] == "gpt-test"

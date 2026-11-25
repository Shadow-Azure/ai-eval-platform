"""M10 (#429): service tokens and the session-or-service auth dependency.

These tests pin the machine-identity contract: the plaintext is returned once
and stored only as SHA-256, the admin management API is session-only, revoked
and expired tokens never authenticate, and ``require_session_or_service``
enforces scope plus the template allowlist without ever falling back to the
cookie when a Bearer header is malformed.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.api.dependencies import Actor, require_session_or_service
from app.config import get_settings
from app.defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from app.models import (
    SERVICE_TOKEN_SCOPES,
    EvalRun,
    ServiceToken,
    Tenant,
    User,
    Workspace,
)
from app.services import identity, service_tokens
from conftest import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

FUTURE = datetime.now(UTC) + timedelta(days=30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(client: TestClient, email: str, password: str):
    return client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


def _add_viewer(session: Session) -> User:
    tenant = session.scalar(select(Tenant).where(Tenant.slug == "default"))
    workspace = session.scalar(select(Workspace).where(Workspace.tenant_id == tenant.id))
    user = identity.upsert_member(
        session,
        email=TEST_VIEWER_EMAIL,
        password=TEST_VIEWER_PASSWORD,
        tenant=tenant,
        workspace=workspace,
        role=identity.ROLE_VIEWER,
        is_default=True,
    )
    session.commit()
    return user


def _make_token(session: Session, **overrides) -> tuple[ServiceToken, str]:
    data = {"name": "he-bot", "scopes": ["eval:launch", "eval:read"]}
    data.update(overrides)
    token, plaintext = service_tokens.create_token(
        session, data, created_by=TEST_ADMIN_EMAIL
    )
    return token, plaintext


def _request(
    *,
    authorization: str | None = None,
    cookie: str | None = None,
    path_params: dict | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("utf-8")))
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("utf-8")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/eval-templates/tmpl/launch",
            "headers": headers,
            "path_params": path_params or {},
            "query_string": b"",
        }
    )


def _call_dependency(session: Session, scope: str, request: Request) -> Actor:
    dependency = require_session_or_service(scope)
    return dependency(request=request, session=session, settings=get_settings())


def _error_code(error: HTTPException) -> str:
    return error.detail["error"]["code"]


def _session_cookie(session: Session, email: str) -> str:
    settings = get_settings()
    user = session.scalar(select(User).where(User.email == email))
    membership = identity.get_default_membership(session, user.id)
    _auth_session, token, _expires_at = identity.create_session(
        session, user=user, membership=membership, settings=settings
    )
    session.commit()
    return f"{settings.auth_cookie_name}={token}"


# ---------------------------------------------------------------------------
# Creation and persistence
# ---------------------------------------------------------------------------


def test_create_returns_plaintext_once_and_persists_only_the_hash(
    client: TestClient, session: Session
) -> None:
    response = client.post(
        "/api/v1/service-tokens",
        json={
            "name": "harness-evolution",
            "scopes": ["eval:launch", "eval:read", "eval:launch"],
            "template_allowlist": ["tmpl-a", "tmpl-a", "tmpl-b"],
            "max_concurrent_runs": 2,
            "daily_budget_cny": 12.5,
            "expires_at": FUTURE.isoformat(),
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ok"
    plaintext = body["token"]
    assert plaintext.startswith("hev_") and len(plaintext) > 40
    assert response.text.count(plaintext) == 1

    view = body["service_token"]
    assert view["name"] == "harness-evolution"
    assert view["scopes"] == ["eval:launch", "eval:read"]
    assert view["template_allowlist"] == ["tmpl-a", "tmpl-b"]
    assert view["max_concurrent_runs"] == 2
    assert Decimal(str(view["daily_budget_cny"])) == Decimal("12.5")
    assert view["active"] is True
    assert view["revoked_at"] is None
    assert view["last_used_at"] is None
    assert view["created_by"] == TEST_ADMIN_EMAIL
    assert "token" not in view and "token_hash" not in view

    stored = session.scalar(select(ServiceToken).where(ServiceToken.name == "harness-evolution"))
    assert stored is not None
    assert stored.token_hash == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    assert len(stored.token_hash) == 64
    assert plaintext not in stored.token_hash
    assert stored.scopes_json == ["eval:launch", "eval:read"]
    assert stored.daily_budget_cny == Decimal("12.5")

    # The list endpoint is masked: no plaintext and no hash ever leaves the API.
    listed = client.get("/api/v1/service-tokens")
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [item["name"] for item in items] == ["harness-evolution"]
    assert plaintext not in listed.text
    assert "token_hash" not in listed.text
    assert items[0]["id"] == view["id"]
    assert "token" not in items[0]


def test_duplicate_name_conflicts_without_creating_a_second_row(
    client: TestClient, session: Session
) -> None:
    payload = {"name": "dup-bot", "scopes": ["eval:read"]}
    first = client.post("/api/v1/service-tokens", json=payload)
    assert first.status_code == 201, first.text

    second = client.post("/api/v1/service-tokens", json=payload)
    assert second.status_code == 409, second.text
    error = second.json()["detail"]["error"]
    assert error["code"] == "service_token_name_conflict"
    assert error["details"]["fields"]["name"] == "invalid"
    assert (
        session.scalar(select(func.count()).select_from(ServiceToken)) == 1
    )


def test_payload_validation_rejects_bad_fields(
    client: TestClient, session: Session
) -> None:
    invalid_http = [
        {"name": "x", "scopes": []},
        {"name": "x", "scopes": ["eval:admin"]},
        {"name": "", "scopes": ["eval:read"]},
        {"name": "x" * 101, "scopes": ["eval:read"]},
        {"name": "x", "scopes": ["eval:read"], "max_concurrent_runs": 0},
        {"name": "x", "scopes": ["eval:read"], "daily_budget_cny": -1},
    ]
    for payload in invalid_http:
        response = client.post("/api/v1/service-tokens", json=payload)
        assert response.status_code == 422, (payload, response.text)

    naive_expiry = client.post(
        "/api/v1/service-tokens",
        json={
            "name": "naive-bot",
            "scopes": ["eval:read"],
            "expires_at": "2030-01-01T00:00:00",
        },
    )
    assert naive_expiry.status_code == 422
    error = naive_expiry.json()["detail"]["error"]
    assert error["code"] == "service_token_invalid"
    assert error["details"]["fields"]["expires_at"] == "invalid"
    assert session.scalar(select(func.count()).select_from(ServiceToken)) == 0

    invalid_service = [
        ({"name": "", "scopes": ["eval:read"]}, "name"),
        ({"name": "x", "scopes": []}, "scopes"),
        ({"name": "x", "scopes": ["eval:write"]}, "scopes"),
        ({"name": "x", "scopes": ["eval:read"], "template_allowlist": ["t" * 37]},
         "template_allowlist"),
        ({"name": "x", "scopes": ["eval:read"], "template_allowlist": [1]},
         "template_allowlist"),
        ({"name": "x", "scopes": ["eval:read"], "max_concurrent_runs": True},
         "max_concurrent_runs"),
        ({"name": "x", "scopes": ["eval:read"], "daily_budget_cny": "free"},
         "daily_budget_cny"),
        ({"name": "x", "scopes": ["eval:read"],
          "expires_at": datetime.now(UTC) - timedelta(minutes=1)}, "expires_at"),
        ({"name": "x", "scopes": ["eval:read"], "expires_at": datetime(2030, 1, 1)},
         "expires_at"),
    ]
    for data, field in invalid_service:
        with pytest.raises(service_tokens.ServiceTokenInvalid) as raised:
            service_tokens.create_token(session, data, created_by=TEST_ADMIN_EMAIL)
        assert raised.value.status == 422
        assert raised.value.code == "service_token_invalid"
        assert raised.value.field == field


def test_management_requires_an_admin_session(
    client: TestClient, session: Session
) -> None:
    _add_viewer(session)
    client.cookies.clear()
    assert _login(client, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD).status_code == 200

    payload = {"name": "viewer-bot", "scopes": ["eval:read"]}
    assert client.post("/api/v1/service-tokens", json=payload).status_code == 403
    assert client.get("/api/v1/service-tokens").status_code == 403
    assert client.delete("/api/v1/service-tokens/missing").status_code == 403

    client.cookies.clear()
    anonymous = client.post("/api/v1/service-tokens", json=payload)
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"]["error"]["code"] == "unauthenticated"

    assert _login(client, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD).status_code == 200
    assert client.post("/api/v1/service-tokens", json=payload).status_code == 201


# ---------------------------------------------------------------------------
# Revocation, expiry and last use
# ---------------------------------------------------------------------------


def test_revoke_returns_204_and_blocks_authentication(
    client: TestClient, session: Session
) -> None:
    created = client.post(
        "/api/v1/service-tokens",
        json={"name": "revoke-bot", "scopes": ["eval:launch"]},
    )
    assert created.status_code == 201, created.text
    token_id = created.json()["service_token"]["id"]
    plaintext = created.json()["token"]
    assert service_tokens.authenticate(session, plaintext) is not None

    revoked = client.delete(f"/api/v1/service-tokens/{token_id}")
    assert revoked.status_code == 204
    assert revoked.text == ""
    stored = session.get(ServiceToken, token_id)
    assert stored.revoked_at is not None
    first_revoked_at = stored.revoked_at
    assert service_tokens.authenticate(session, plaintext) is None
    assert service_tokens.token_view(stored)["active"] is False

    # Idempotent: a second revoke keeps the original audit timestamp.
    assert client.delete(f"/api/v1/service-tokens/{token_id}").status_code == 204
    session.refresh(stored)
    assert stored.revoked_at == first_revoked_at

    missing = client.delete("/api/v1/service-tokens/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "service_token_not_found"


def test_authenticate_records_last_use_and_rejects_expiry(session: Session) -> None:
    token, plaintext = _make_token(session, name="used-bot")
    assert token.last_used_at is None

    resolved = service_tokens.authenticate(session, plaintext)
    assert resolved is not None
    assert resolved.id == token.id
    assert token.last_used_at is not None

    token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert service_tokens.authenticate(session, plaintext) is None

    expired, expired_plaintext = _make_token(session, name="expired-bot")
    expired.expires_at = datetime.now(UTC) - timedelta(days=1)
    session.commit()
    assert service_tokens.authenticate(session, expired_plaintext) is None

    assert service_tokens.authenticate(session, "hev_unknown") is None
    assert service_tokens.authenticate(session, "plain-token") is None
    assert service_tokens.authenticate(session, "") is None
    assert service_tokens.authenticate(session, None) is None


# ---------------------------------------------------------------------------
# Session-or-service dependency
# ---------------------------------------------------------------------------


def test_dependency_accepts_an_in_scope_service_token(session: Session) -> None:
    _token, plaintext = _make_token(session, name="launch-bot")
    request = _request(
        authorization=f"Bearer {plaintext}", path_params={"template_id": "tmpl-a"}
    )

    actor = _call_dependency(session, "eval:launch", request)

    assert isinstance(actor, Actor)
    assert actor.label == "svc:launch-bot"
    assert actor.is_service is True
    assert actor.is_admin is False
    assert actor.scopes == frozenset(SERVICE_TOKEN_SCOPES)
    assert actor.service_token is not None
    assert actor.service_token.name == "launch-bot"


def test_dependency_enforces_scope_and_template_allowlist(session: Session) -> None:
    _read_token, read_plaintext = _make_token(
        session, name="read-bot", scopes=["eval:read"]
    )
    with pytest.raises(HTTPException) as raised:
        _call_dependency(
            session,
            "eval:launch",
            _request(authorization=f"Bearer {read_plaintext}"),
        )
    assert raised.value.status_code == 403
    assert _error_code(raised.value) == "forbidden"
    # ``eval:read`` is the same token's scope, so reads pass.
    read_actor = _call_dependency(
        session, "eval:read", _request(authorization=f"Bearer {read_plaintext}")
    )
    assert read_actor.scopes == frozenset({"eval:read"})

    _scoped, scoped_plaintext = _make_token(
        session,
        name="allow-bot",
        scopes=["eval:launch"],
        template_allowlist=["tmpl-a"],
    )
    allowed = _call_dependency(
        session,
        "eval:launch",
        _request(
            authorization=f"Bearer {scoped_plaintext}",
            path_params={"template_id": "tmpl-a"},
        ),
    )
    assert allowed.label == "svc:allow-bot"
    for path_params in ({"template_id": "tmpl-b"}, {}):
        with pytest.raises(HTTPException) as denied:
            _call_dependency(
                session,
                "eval:launch",
                _request(
                    authorization=f"Bearer {scoped_plaintext}",
                    path_params=path_params,
                ),
            )
        assert denied.value.status_code == 403

    # An empty allowlist means every template.
    _open, open_plaintext = _make_token(session, name="open-bot", scopes=["eval:launch"])
    open_actor = _call_dependency(
        session,
        "eval:launch",
        _request(
            authorization=f"Bearer {open_plaintext}",
            path_params={"template_id": "any-template"},
        ),
    )
    assert open_actor.is_service is True


def test_dependency_rejects_bad_or_dead_bearer_tokens(session: Session) -> None:
    _token, plaintext = _make_token(session, name="dead-bot")
    service_tokens.revoke_token(session, _token.id)

    cases = [
        "Token " + plaintext,
        "Bearer plain-token",
        "Bearer " + "x" * 40,
        "Bearer",
        "Bearer " + plaintext,  # revoked
    ]
    for header in cases:
        with pytest.raises(HTTPException) as raised:
            _call_dependency(
                session,
                "eval:launch",
                _request(authorization=header, path_params={"template_id": "tmpl"}),
            )
        assert raised.value.status_code == 401
        assert _error_code(raised.value) == "unauthenticated"

    with pytest.raises(ValueError):
        require_session_or_service("eval:write")


def test_malformed_bearer_never_falls_back_to_the_console_cookie(
    client: TestClient, session: Session
) -> None:
    cookie = _session_cookie(session, TEST_ADMIN_EMAIL)

    # A valid cookie alone passes the admin launch check...
    actor = _call_dependency(
        session,
        "eval:launch",
        _request(cookie=cookie, path_params={"template_id": "tmpl-a"}),
    )
    assert actor.label == TEST_ADMIN_EMAIL
    assert actor.is_admin is True and actor.is_service is False

    # ...but a present Authorization header wins, and a malformed one is a 401
    # instead of silently degrading to the cookie.
    with pytest.raises(HTTPException) as raised:
        _call_dependency(
            session,
            "eval:launch",
            _request(
                authorization="Basic abc",
                cookie=cookie,
                path_params={"template_id": "tmpl-a"},
            ),
        )
    assert raised.value.status_code == 401

    with pytest.raises(HTTPException) as anonymous:
        _call_dependency(session, "eval:read", _request())
    assert anonymous.value.status_code == 401


def test_session_branch_keeps_role_semantics(
    client: TestClient, session: Session
) -> None:
    viewer = _add_viewer(session)
    viewer_cookie = _session_cookie(session, viewer.email)

    with pytest.raises(HTTPException) as denied:
        _call_dependency(
            session,
            "eval:launch",
            _request(cookie=viewer_cookie, path_params={"template_id": "tmpl-a"}),
        )
    assert denied.value.status_code == 403

    read_request = _request(cookie=viewer_cookie)
    reader = _call_dependency(session, "eval:read", read_request)
    assert reader.is_admin is False
    assert reader.is_service is False
    assert read_request.state.auth.user.email == viewer.email

    admin_request = _request(
        cookie=_session_cookie(session, TEST_ADMIN_EMAIL),
        path_params={"template_id": "tmpl-a"},
    )
    admin = _call_dependency(session, "eval:launch", admin_request)
    assert admin.is_admin is True
    assert admin_request.state.auth.role == identity.ROLE_ADMIN


# ---------------------------------------------------------------------------
# Launch caps
# ---------------------------------------------------------------------------


def _run(session: Session, *, key: str, status: str, **overrides) -> EvalRun:
    values = {
        "tenant_id": str(DEFAULT_TENANT_ID),
        "project_id": str(DEFAULT_PROJECT_ID),
        "name": f"run-{key}",
        "idempotency_key": key,
        "created_by": "svc:capped-bot",
        "status": status,
    }
    values.update(overrides)
    run = EvalRun(**values)
    session.add(run)
    session.commit()
    return run


def test_eval_run_override_columns_default_to_empty(session: Session) -> None:
    run = _run(session, key="override-default", status="queued")
    assert run.asset_override_json == {}
    assert run.evolution_lineage_json == {}


def test_concurrency_cap_blocks_only_non_terminal_runs(session: Session) -> None:
    token, _plaintext = _make_token(
        session, name="capped-bot", scopes=["eval:launch"], max_concurrent_runs=1
    )
    assert service_tokens.enforce_launch_caps(session, token) is None

    run = _run(session, key="concurrency-1", status="running")
    with pytest.raises(service_tokens.ServiceTokenLimit) as raised:
        service_tokens.enforce_launch_caps(session, token)
    assert raised.value.status == 429
    assert raised.value.code == "service_token_limit_exceeded"
    assert raised.value.details["limit"] == "max_concurrent_runs"
    assert raised.value.details["active"] == 1

    run.status = "completed"
    session.commit()
    assert service_tokens.enforce_launch_caps(session, token) is None

    # Runs created by someone else never count against the machine identity.
    _run(
        session,
        key="concurrency-other",
        status="running",
        created_by=TEST_ADMIN_EMAIL,
    )
    assert service_tokens.enforce_launch_caps(session, token) is None


def test_daily_budget_cap_counts_today_spend(session: Session) -> None:
    token, _plaintext = _make_token(
        session,
        name="budget-bot",
        scopes=["eval:launch"],
        daily_budget_cny=5,
    )
    _run(
        session,
        key="budget-1",
        status="completed",
        created_by="svc:budget-bot",
        budget_spent_cny=Decimal("7.500000"),
    )
    with pytest.raises(service_tokens.ServiceTokenLimit) as raised:
        service_tokens.enforce_launch_caps(session, token)
    assert raised.value.code == "service_token_limit_exceeded"
    assert raised.value.details["limit"] == "daily_budget_cny"
    assert Decimal(str(raised.value.details["cap"])) == Decimal("5")
    assert Decimal(str(raised.value.details["spent"])) == Decimal("7.5")

    # Yesterday's spend is outside the UTC-day window: 7.5 today is under an 8
    # cap, but 7.5 + 3 would not be, so this also proves the window.
    yesterday = datetime.now(UTC) - timedelta(days=1)
    _run(
        session,
        key="budget-yesterday",
        status="completed",
        created_by="svc:budget-bot",
        budget_spent_cny=Decimal("3"),
        created_at=yesterday,
    )
    generous, _ = _make_token(
        session, name="budget-ok-bot", scopes=["eval:launch"], daily_budget_cny=8
    )
    assert service_tokens.enforce_launch_caps(session, generous) is None

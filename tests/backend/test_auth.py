"""M3-05 account-password login and session access control."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.models import AuditLog, AuthSession, Membership, Tenant, User, Workspace
from app.seed import ensure_bootstrap_admin, ensure_default_scope
from app.services import identity
from conftest import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def login(client: TestClient, email: str, password: str, *, remember: bool = False):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "remember": remember},
    )


def set_cookie(client: TestClient, token: str) -> None:
    client.cookies.set("eval_session", token)


def add_viewer(session: Session) -> User:
    tenant = session.scalar(select(Tenant).where(Tenant.slug == "default"))
    workspace = session.scalar(
        select(Workspace).where(Workspace.tenant_id == tenant.id)
    )
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


# ---------------------------------------------------------------------------
# Password hashing and bootstrap
# ---------------------------------------------------------------------------


def test_password_hash_is_salted_scrypt_and_verifiable() -> None:
    first = identity.hash_password("correct horse battery staple")
    second = identity.hash_password("correct horse battery staple")

    assert first.startswith("scrypt$")
    assert first != second
    assert "correct horse" not in first
    assert identity.verify_password("correct horse battery staple", first)
    assert not identity.verify_password("wrong password", first)
    assert not identity.verify_password("correct horse battery staple", "not-a-hash")


def test_bootstrap_admin_is_idempotent_and_never_plaintext(
    engine, configure_settings
) -> None:
    configure_settings(
        AUTH_BOOTSTRAP_ADMIN_EMAIL="bootstrap@test.local",
        AUTH_BOOTSTRAP_ADMIN_PASSWORD="bootstrap-password",
    )
    with Session(engine) as session:
        tenant, workspace, _project = ensure_default_scope(session)
        first = ensure_bootstrap_admin(session, tenant, workspace)
        second = ensure_bootstrap_admin(session, tenant, workspace)

        assert first is not None and second is not None
        assert first.id == second.id
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(Membership)) == 1
        user = session.scalar(select(User))
        assert user.email == "bootstrap@test.local"
        assert user.password_hash != "bootstrap-password"
        assert identity.verify_password("bootstrap-password", user.password_hash)
        membership = session.scalar(select(Membership))
        assert membership.role == identity.ROLE_ADMIN
        assert membership.is_default is True


def test_bootstrap_without_password_is_a_noop(engine, configure_settings) -> None:
    configure_settings(AUTH_BOOTSTRAP_ADMIN_PASSWORD="")
    with Session(engine) as session:
        tenant, workspace, _project = ensure_default_scope(session)
        assert ensure_bootstrap_admin(session, tenant, workspace) is None
        assert session.scalar(select(func.count()).select_from(User)) == 0


# ---------------------------------------------------------------------------
# Login / me / logout
# ---------------------------------------------------------------------------


def test_login_sets_httponly_cookie_and_me_returns_context(
    anonymous_client: TestClient,
) -> None:
    response = login(anonymous_client, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == TEST_ADMIN_EMAIL
    assert body["tenant"]["slug"] == "default"
    assert body["workspace"]["slug"] == "default"
    assert body["role"] == identity.ROLE_ADMIN
    assert "password" not in response.text
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert anonymous_client.cookies.get("eval_session")

    me = anonymous_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == body


@pytest.mark.parametrize(
    ("email", "password"),
    [
        (TEST_ADMIN_EMAIL, "wrong-password"),
        ("unknown@test.local", "irrelevant"),
    ],
)
def test_login_failure_is_generic(
    anonymous_client: TestClient, email: str, password: str
) -> None:
    response = login(anonymous_client, email, password)
    assert response.status_code == 401
    error = response.json()["detail"]["error"]
    assert error["code"] == "invalid_credentials"
    assert error["message"] == "账号或密码不正确。"
    assert anonymous_client.cookies.get("eval_session") is None


def test_disabled_user_cannot_login(
    anonymous_client: TestClient, session: Session
) -> None:
    user = session.scalar(select(User).where(User.email == TEST_ADMIN_EMAIL))
    user.is_active = False
    session.commit()

    response = login(anonymous_client, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "invalid_credentials"


def test_logout_revokes_the_session_and_old_cookie_is_rejected(
    anonymous_client: TestClient, session: Session
) -> None:
    assert login(anonymous_client, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD).status_code == 200
    token = anonymous_client.cookies.get("eval_session")
    assert token

    logout = anonymous_client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {"status": "ok"}

    auth_session = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == identity.session_token_hash(token)
        )
    )
    assert auth_session.revoked_at is not None

    set_cookie(anonymous_client, token)
    assert anonymous_client.get("/api/v1/auth/me").status_code == 401


def test_expired_and_tampered_tokens_are_rejected(
    anonymous_client: TestClient, session: Session
) -> None:
    assert login(anonymous_client, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD).status_code == 200
    token = anonymous_client.cookies.get("eval_session")
    assert token

    set_cookie(anonymous_client, token + "tampered")
    assert anonymous_client.get("/api/v1/auth/me").status_code == 401

    auth_session = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == identity.session_token_hash(token)
        )
    )
    auth_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    set_cookie(anonymous_client, token)
    assert anonymous_client.get("/api/v1/auth/me").status_code == 401


def test_login_writes_an_audit_entry(
    anonymous_client: TestClient, session: Session
) -> None:
    session.query(AuditLog).delete()
    session.commit()
    assert login(anonymous_client, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD).status_code == 200
    assert anonymous_client.post("/api/v1/auth/logout").status_code == 200

    actions = [
        entry.action
        for entry in session.scalars(select(AuditLog).order_by(AuditLog.created_at)).all()
    ]
    assert actions == ["auth.login", "auth.logout"]


def test_password_reset_revokes_existing_sessions(
    anonymous_client: TestClient, session: Session
) -> None:
    add_viewer(session)
    assert login(anonymous_client, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD).status_code == 200
    token = anonymous_client.cookies.get("eval_session")
    assert token

    updated = identity.set_password(
        session, email=TEST_VIEWER_EMAIL, password="new-viewer-password"
    )
    assert updated is not None
    session.commit()

    set_cookie(anonymous_client, token)
    assert anonymous_client.get("/api/v1/auth/me").status_code == 401
    anonymous_client.cookies.clear()
    assert login(anonymous_client, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD).status_code == 401
    assert login(anonymous_client, TEST_VIEWER_EMAIL, "new-viewer-password").status_code == 200


# ---------------------------------------------------------------------------
# Console access control
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/overview",
        "/api/v1/evaluations",
        "/api/v1/datasets",
        "/api/v1/runners",
    ],
)
def test_console_apis_require_a_session(anonymous_client: TestClient, path: str) -> None:
    response = anonymous_client.get(path)
    assert response.status_code == 401
    error = response.json()["detail"]["error"]
    assert error["code"] == "unauthenticated"
    assert error["message"]


def test_runner_plane_is_not_gated_by_the_console_session(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.post(
        "/runner/v1/runners/heartbeat",
        json={
            "runner_id": "auth-test-runner",
            "name": "auth-test-runner",
            "labels": ["self-hosted"],
            "status": "online",
            "current_case_attempt_id": None,
        },
    )
    assert response.status_code == 200


def test_viewer_cannot_perform_admin_writes(
    anonymous_client: TestClient, session: Session
) -> None:
    add_viewer(session)
    anonymous_client.cookies.clear()
    assert login(anonymous_client, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD).status_code == 200

    create = anonymous_client.post(
        "/api/v1/evaluations", json={"case_keys": ["case-a"], "idempotency_key": "viewer-1"}
    )
    assert create.status_code == 403
    assert create.json()["detail"]["error"]["code"] == "forbidden"

    import_response = anonymous_client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": "swebench",
            "dataset_name": "SWE-bench",
            "adapter_key": "swebench",
            "version": "v1",
            "source_revision": "rev-1",
            "records": [{"instance_id": "x"}],
        },
    )
    assert import_response.status_code == 403

    anonymous_client.cookies.clear()
    assert login(anonymous_client, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD).status_code == 200
    admin_create = anonymous_client.post(
        "/api/v1/evaluations", json={"case_keys": ["case-a"], "idempotency_key": "admin-1"}
    )
    assert admin_create.status_code == 201

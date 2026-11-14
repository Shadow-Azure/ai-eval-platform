from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

TEST_AUTH_SECRET = "test-auth-session-secret"
TEST_ADMIN_EMAIL = "admin@test.local"
TEST_ADMIN_PASSWORD = "test-admin-password"
TEST_VIEWER_EMAIL = "viewer@test.local"
TEST_VIEWER_PASSWORD = "test-viewer-password"

# Settings are cached on import, so point the default app at a harmless local
# SQLite file before importing app modules. Every test overrides the session
# dependency with its own migrated database.
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/ai-eval-platform-tests-default.db")
os.environ.setdefault("REDIS_URL", "")
# Dataset tests must not depend on public registry/network availability.
os.environ.setdefault("DATASET_IMAGE_REGISTRIES", "")

from app.config import get_settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402


def run_alembic(url: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed with {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def prepare_database(url: str) -> None:
    """Reset a disposable test database before applying migrations from empty."""

    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).unlink(missing_ok=True)
    else:
        if "test" not in url.lower():
            raise RuntimeError("TEST_DATABASE_URL must point to a disposable *test* database")
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()
    run_alembic(url, "upgrade", "head")


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _ddl(engine, statement: str) -> None:
    with engine.connect() as connection:
        connection.execute(text(statement))


def _drop_database(engine, name: str) -> None:
    _ddl(engine, f"DROP DATABASE IF EXISTS {_quoted(name)} WITH (FORCE)")


def _create_database(engine, name: str, template: str | None = None) -> None:
    source = f" TEMPLATE {_quoted(template)}" if template else ""
    _ddl(engine, f"CREATE DATABASE {_quoted(name)}{source}")


@pytest.fixture(scope="session")
def database_template(tmp_path_factory: pytest.TempPathFactory):
    """Migrate one template database per session and clone it per test.

    PostgreSQL is cloned with CREATE DATABASE ... TEMPLATE; SQLite by copying the
    migrated file.  If the test role cannot create databases, fall back to the
    original per-test migration so local PostgreSQL setups keep working.
    """

    external = os.environ.get("TEST_DATABASE_URL")
    if not external:
        template_path = tmp_path_factory.mktemp("db-template") / "template.db"
        run_alembic(f"sqlite:///{template_path}", "upgrade", "head")
        yield ("sqlite", template_path)
        return

    parsed = make_url(external)
    if "test" not in external.lower():
        raise RuntimeError("TEST_DATABASE_URL must point to a disposable *test* database")
    prefix = (parsed.database or "eval_test")[:40]
    template = f"{prefix}_tmpl"
    admin = create_engine(external, future=True, isolation_level="AUTOCOMMIT")
    try:
        try:
            _drop_database(admin, template)
            _create_database(admin, template)
        except Exception as error:  # pragma: no cover - depends on the PostgreSQL role
            if os.environ.get("CI"):
                raise
            warnings.warn(
                f"cannot create PostgreSQL template database ({error}); "
                "falling back to per-test migrations",
                stacklevel=2,
            )
            yield ("fallback", external)
            return
        template_url = parsed.set(database=template).render_as_string(hide_password=False)
        try:
            run_alembic(template_url, "upgrade", "head")
            yield ("postgres", external, template)
        finally:
            _drop_database(admin, template)
    finally:
        admin.dispose()


@pytest.fixture
def database_url(database_template, tmp_path: Path) -> str:
    kind = database_template[0]
    if kind == "fallback":
        external = database_template[1]
        prepare_database(external)
        yield external
        return
    if kind == "sqlite":
        template_path = database_template[1]
        path = tmp_path / "test.db"
        shutil.copyfile(template_path, path)
        yield f"sqlite:///{path}"
        return

    admin_url = database_template[1]
    template = database_template[2]
    parsed = make_url(admin_url)
    prefix = (parsed.database or "eval_test")[:40]
    name = f"{prefix}_t_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        _create_database(admin, name, template=template)
        yield parsed.set(database=name).render_as_string(hide_password=False)
    finally:
        _drop_database(admin, name)
        admin.dispose()


@pytest.fixture
def engine(database_url: str):
    created = create_engine(database_url, future=True)

    if created.dialect.name == "sqlite":

        @event.listens_for(created, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    yield created
    created.dispose()


@pytest.fixture
def session(engine) -> Session:
    with Session(engine) as db_session:
        yield db_session


@pytest.fixture
def configure_settings(monkeypatch: pytest.MonkeyPatch):
    """Override Settings via environment (they are cached with lru_cache)."""

    def _configure(**overrides: str) -> None:
        for key, value in overrides.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()

    yield _configure
    get_settings.cache_clear()


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Authenticated console client.

    Console APIs now require a session (M3-05); seed the default scope plus an
    administrator and log in so existing API tests keep exercising their own
    concerns. Tests that need the unauthenticated path clear ``client.cookies``.
    """

    monkeypatch.setenv("AUTH_SESSION_SECRET", TEST_AUTH_SECRET)
    monkeypatch.setenv("AUTH_BOOTSTRAP_ADMIN_EMAIL", TEST_ADMIN_EMAIL)
    monkeypatch.setenv("AUTH_BOOTSTRAP_ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    get_settings.cache_clear()

    testing_session = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, class_=Session
    )

    def override_get_session():
        with testing_session() as db_session:
            yield db_session

    from app.seed import ensure_bootstrap_admin, ensure_default_scope

    with testing_session() as seed_session:
        tenant, workspace, _project = ensure_default_scope(seed_session)
        ensure_bootstrap_admin(seed_session, tenant, workspace)

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        login = test_client.post(
            "/api/v1/auth/login",
            json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
        )
        assert login.status_code == 200, login.text
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def anonymous_client(client: TestClient) -> TestClient:
    """The shared client with its session cookie removed."""

    client.cookies.clear()
    return client


EVAL_ENV_SECRET = "platform-task-secret"


@pytest.fixture
def eval_env(session: Session, client: TestClient, configure_settings):
    """One seeded synthetic evaluation (2 cases, 1 shard) with token auth and
    a local stub object store; the fixed runner registers via heartbeat first
    so X-Runner-Id bindings resolve like on the host. Shared by the callback
    and upload-urls suites."""

    from app.models import EvalCaseAttempt, EvalCaseRun
    from app.seed import ensure_default_scope
    from app.services import evaluations
    from case_helpers import EVAL_ENV_SECRET as SECRET
    from object_stub import StubObjectStore

    configure_settings(RUNNER_TASK_SECRET=SECRET)
    ensure_default_scope(session)
    registered = client.post(
        "/runner/v1/runners/heartbeat",
        json={
            "runner_id": "fixed-linux-eval",
            "name": "fixed-linux-eval",
            "labels": ["self-hosted", "linux", "eval"],
            "status": "online",
            "current_case_attempt_id": None,
        },
    )
    assert registered.status_code == 200
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        run, _ = evaluations.create_synthetic_evaluation(
            session,
            name="回调冒烟",
            case_keys=["case-a", "case-b"],
            idempotency_key="cb-env",
            created_by="agent",
            settings=get_settings(),
        )
        cases = {
            c.case_key: c.id
            for c in session.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id).all()
        }
        yield {
            "session": session,
            "run_id": run.id,
            "cases": cases,
            "store": store,
            "shard": session.query(EvalCaseAttempt)
            .filter(EvalCaseAttempt.eval_case_run_id == cases["case-a"])
            .first()
            .shard_id,
        }

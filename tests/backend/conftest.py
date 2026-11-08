from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

# Settings are cached on import, so point the default app at a harmless local
# SQLite file before importing app modules. Every test overrides the session
# dependency with its own migrated database.
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/ai-eval-platform-tests-default.db")
os.environ.setdefault("REDIS_URL", "")

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


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    external = os.environ.get("TEST_DATABASE_URL")
    if external:
        prepare_database(external)
        yield external
        return
    path = tmp_path / "test.db"
    url = f"sqlite:///{path}"
    prepare_database(url)
    yield url


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
def client(engine) -> TestClient:
    testing_session = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, class_=Session
    )

    def override_get_session():
        with testing_session() as db_session:
            yield db_session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


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

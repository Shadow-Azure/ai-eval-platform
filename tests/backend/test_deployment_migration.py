from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_deployment_table_and_revision(database_url: str) -> None:
    engine = create_engine(database_url, future=True)
    try:
        inspector = inspect(engine)
        assert "deployment" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("deployment")}
        assert {
            "id",
            "environment",
            "git_commit",
            "image_digest",
            "migration_revision",
            "status",
            "triggered_by",
            "started_at",
            "finished_at",
            "rollback_of_id",
            "detail_json",
        } <= columns
        with Session(engine) as session:
            revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            assert revision == "0018_billing_cny"
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="record_deployment.py targets PostgreSQL",
)
def test_record_deployment_script_inserts_a_row(database_url: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": database_url,
            "DEPLOY_STATUS": "succeeded",
            "DEPLOY_ENVIRONMENT": "test",
            "GIT_COMMIT": "abc1234",
            "DEPLOY_IMAGE_DIGEST": "registry.example/api@sha256:" + "0" * 64,
            "DEPLOY_MIGRATION_REVISION": "0002_deployment",
            "DEPLOY_TRIGGERED_BY": "pytest",
            "DEPLOY_DETAIL_API_IMAGE": "registry.example/api@sha256:" + "1" * 64,
            "DEPLOY_DETAIL_WEB_IMAGE": "registry.example/web@sha256:" + "2" * 64,
            "DEPLOY_DETAIL_GIT_COMMIT": "abc1234",
        }
    )
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "backend" / "migrations" / "record_deployment.py")],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    engine = create_engine(database_url, future=True)
    try:
        with Session(engine) as session:
            row = session.execute(
                text(
                    "SELECT environment, git_commit, status, detail_json "
                    "FROM deployment WHERE git_commit = 'abc1234'"
                )
            ).first()
            assert row is not None
            assert row.environment == "test"
            assert row.status == "succeeded"
            assert row.detail_json == {
                "api_image": "registry.example/api@sha256:" + "1" * 64,
                "web_image": "registry.example/web@sha256:" + "2" * 64,
                "git_commit": "abc1234",
            }
    finally:
        engine.dispose()

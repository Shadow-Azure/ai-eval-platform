from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from app.models import Project, Tenant, Workspace
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"


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
        raise AssertionError(f"alembic {' '.join(args)} failed: {result.stderr}")
    return result


EXPECTED_TABLES = {
    "tenant",
    "workspace",
    "project",
    "resource_pool",
    "connection",
    "runner",
    "provisioned_instance",
    # M4-03 资源连接与竞价资源池容量事件
    "resource_event",
    "eval_run",
    "eval_case_run",
    "eval_case_attempt",
    "eval_event",
    "outbox",
    # M3-01 数据集切片与指标注册（design 05 §4）
    "dataset",
    "dataset_version",
    "dataset_case",
    "dataset_import",
    "metric_definition",
    "dataset_version_metric",
    # M3-05 账号与会话（design 05 §3）
    "user",
    "membership",
    "auth_session",
    "audit_log",
    # M3-02 模型凭据、网关配置与用量（design 04 §7 / 05 §3）
    "credential",
    "model_config",
    "model_usage",
    # M3-04 真实评测结果与测试级打点
    "score_artifact",
    "test_case_result",
    "metric_result",
    # M3-06 管理员运营状态：Worker 心跳
    "service_heartbeat",
    # M5-01 Agent 资产源、不可变快照与快照资产
    "agent_asset_source",
    "agent_asset_snapshot",
    "agent_asset",
    # M6-02 版本化轨迹原件、session 与 span
    "trace_file",
    "trace_session",
    "trace_span",
}


def test_empty_database_migrates_to_head(database_url: str) -> None:
    run_alembic(database_url, "upgrade", "head")
    engine = create_engine(database_url, future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES <= tables
        dataset_version_columns = {
            column["name"] for column in inspect(engine).get_columns("dataset_version")
        }
        assert {"execution_image_digest", "scoring_image_digest"} <= dataset_version_columns
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Tenant)) == 1
            assert session.scalar(select(func.count()).select_from(Workspace)) == 1
            assert session.scalar(select(func.count()).select_from(Project)) == 1
            assert session.scalar(select(Tenant.slug)) == "default"
    finally:
        engine.dispose()


def test_repeated_upgrade_is_a_noop(database_url: str) -> None:
    first = run_alembic(database_url, "upgrade", "head")
    second = run_alembic(database_url, "upgrade", "head")
    assert first.returncode == 0
    assert second.returncode == 0
    assert "Running upgrade" not in second.stderr


def test_downgrade_then_upgrade_restores_schema_and_seed(database_url: str) -> None:
    run_alembic(database_url, "downgrade", "base")
    run_alembic(database_url, "upgrade", "head")
    engine = create_engine(database_url, future=True)
    try:
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Tenant)) == 1
            assert session.scalar(select(func.count()).select_from(Project)) == 1
    finally:
        engine.dispose()


def test_invalid_revision_fails_clearly(database_url: str) -> None:
    result = run_alembic(database_url, "upgrade", "does_not_exist", check=False)
    assert result.returncode != 0
    assert "does_not_exist" in (result.stderr + result.stdout)


def test_revision_ids_fit_alembic_version_column() -> None:
    """Alembic stores revision ids in alembic_version.version_num VARCHAR(32)."""

    versions_dir = REPO_ROOT / "backend" / "migrations" / "versions"
    for path in sorted(versions_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^revision(?:: str)? = ['\"]([^'\"]+)['\"]", text, re.MULTILINE)
        assert match is not None, path.name
        revision = match.group(1)
        assert len(revision) <= 32, (
            f"{path.name}: revision id {revision!r} exceeds "
            "alembic_version.version_num VARCHAR(32)"
        )

def test_agent_asset_load_test_column_roundtrip(database_url: str) -> None:
    """0023 adds the load-test result column and its downgrade removes it."""

    run_alembic(database_url, "upgrade", "head")
    engine = create_engine(database_url, future=True)
    try:
        columns = {
            column["name"] for column in inspect(engine).get_columns("agent_asset_source")
        }
        assert "load_test_json" in columns
    finally:
        engine.dispose()

    run_alembic(database_url, "downgrade", "0022_eval_run_prompt_suffix")
    engine = create_engine(database_url, future=True)
    try:
        columns = {
            column["name"] for column in inspect(engine).get_columns("agent_asset_source")
        }
        assert "load_test_json" not in columns
    finally:
        engine.dispose()

    run_alembic(database_url, "upgrade", "head")


def test_trace_tables_migrate_from_dataset_import_head(database_url: str) -> None:
    """0021 must apply on top of 0020 and reverse without losing earlier state."""

    run_alembic(database_url, "upgrade", "0020_dataset_import_upload")
    run_alembic(database_url, "upgrade", "head")
    engine = create_engine(database_url, future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"trace_file", "trace_session", "trace_span"} <= tables
        assert "trace_status" in {
            column["name"] for column in inspect(engine).get_columns("eval_case_run")
        }
        session_columns = {
            column["name"] for column in inspect(engine).get_columns("trace_session")
        }
        assert "native_session_key" in session_columns
        file_columns = {
            column["name"] for column in inspect(engine).get_columns("trace_file")
        }
        assert "lease_owner" in file_columns
        session_fks = inspect(engine).get_foreign_keys("trace_session")
        assert any(
            fk["constrained_columns"] == ["tenant_id", "parent_session_id"]
            and fk["referred_columns"] == ["tenant_id", "id"]
            for fk in session_fks
        )
        span_fks = inspect(engine).get_foreign_keys("trace_span")
        assert any(
            fk["constrained_columns"] == ["tenant_id", "parent_span_id"]
            and fk["referred_columns"] == ["tenant_id", "id"]
            for fk in span_fks
        )
        span_indexes = {index["name"]: index for index in inspect(engine).get_indexes("trace_span")}
        assert span_indexes["uq_trace_span_session_event"]["unique"] == 1
        assert "ix_trace_span_parent_span_id" in span_indexes
    finally:
        engine.dispose()

    run_alembic(database_url, "downgrade", "0020_dataset_import_upload")
    engine = create_engine(database_url, future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert not {"trace_file", "trace_session", "trace_span"} & tables
        assert "trace_status" in {
            column["name"] for column in inspect(engine).get_columns("eval_case_run")
        }
    finally:
        engine.dispose()

    run_alembic(database_url, "upgrade", "head")


def test_eval_run_max_turns_column_roundtrip(database_url: str) -> None:
    """0026 adds the run-level turn cap with the 200 default and reverses it."""

    run_alembic(database_url, "upgrade", "0026_eval_run_max_turns")
    engine = create_engine(database_url, future=True)
    try:
        columns = {
            column["name"]: column for column in inspect(engine).get_columns("eval_run")
        }
        assert "max_turns" in columns
        assert columns["max_turns"]["nullable"] is False
        assert str(columns["max_turns"]["default"]).strip("'") == "200"
    finally:
        engine.dispose()

    run_alembic(database_url, "downgrade", "0025_dataset_image_sources")
    engine = create_engine(database_url, future=True)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("eval_run")}
        assert "max_turns" not in columns
    finally:
        engine.dispose()

    run_alembic(database_url, "upgrade", "head")

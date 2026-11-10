from __future__ import annotations

import os
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
}


def test_empty_database_migrates_to_head(database_url: str) -> None:
    run_alembic(database_url, "upgrade", "head")
    engine = create_engine(database_url, future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES <= tables
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

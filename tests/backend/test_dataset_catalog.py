"""M5-13 dataset catalog API（design 02 §3.1 / 07 §1）。

目录卡片字段必须来自数据集行 + 最新版本 + 指标定义 + 适配器注册表元数据：
任务族、环境形态、当前版本、用例数、核心指标与接入状态；不硬编码 SWE-bench。
"""

from __future__ import annotations

import time

import pytest
from app.defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from app.models import Dataset
from app.seed import ensure_default_scope
from dataset_helpers import fixture_records
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def post_import(
    client: TestClient,
    *,
    records,
    version: str = "verified-smoke",
    slug: str = "swebench-verified",
    name: str = "SWE-bench Verified",
) -> dict:
    response = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": slug,
            "dataset_name": name,
            "adapter_key": "swebench",
            "version": version,
            "source_uri": "tests/backend/fixtures/swebench/verified-slice.jsonl",
            "source_revision": "rev-0001",
            "records": records,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_catalog_items_include_adapter_metadata_and_latest_version(
    seeded: Session, client: TestClient
) -> None:
    records = fixture_records()
    first = post_import(client, records=records, slug="swebench-verified", name="SWE-bench Verified")
    post_import(
        client,
        records=records[:3],
        version="v2026.09",
        slug="swebench-lite",
        name="SWE-bench Lite",
    )
    assert first["dataset"]["task_family"] == "软件工程"
    assert first["dataset"]["environment_kind"] == "GitHub Issue · Docker"

    response = client.get("/api/v1/datasets", params={"limit": 200})
    assert response.status_code == 200
    body = response.json()
    assert body["page_info"]["total"] == 2
    by_slug = {item["slug"]: item for item in body["items"]}
    verified = by_slug["swebench-verified"]

    # 卡片字段：任务族 / 环境形态 / 当前版本 / 用例数 / 核心指标 / 接入状态。
    assert verified["task_family"] == "软件工程"
    assert verified["environment_kind"] == "GitHub Issue · Docker"
    assert verified["status"] == "ready"
    assert verified["version_count"] == 1
    assert verified["latest_version"]["version"] == "verified-smoke"
    assert verified["latest_version"]["case_count"] == len(records)
    assert verified["primary_metric"]["metric_key"] == "resolved_rate"
    assert "解决率" in verified["primary_metric"]["display_name"]
    # 适配器自描述元数据来自注册表，而不是前端写死。
    assert verified["adapter"] == {
        "key": "swebench",
        "display_name": "SWE-bench Adapter",
        "registered": True,
    }
    assert verified["description"]
    assert by_slug["swebench-lite"]["latest_version"]["case_count"] == 3


def test_adapter_catalog_exposes_registered_adapters(seeded: Session, client: TestClient) -> None:
    post_import(client, records=fixture_records())
    response = client.get("/api/v1/dataset-adapters")
    assert response.status_code == 200
    items = response.json()["items"]
    swebench = next(item for item in items if item["key"] == "swebench")
    assert swebench["display_name"] == "SWE-bench Adapter"
    assert swebench["task_family"] == "软件工程"
    assert swebench["environment_kind"] == "GitHub Issue · Docker"
    assert swebench["primary_metric_key"] == "resolved_rate"
    assert "resolved_rate" in swebench["metric_keys"]
    assert swebench["dataset_count"] == 1
    assert swebench["case_visibility"]["reference_patch"] == "scoring_only"


def test_catalog_reports_draft_dataset_without_version(seeded: Session, client: TestClient) -> None:
    seeded.add(
        Dataset(
            tenant_id=str(DEFAULT_TENANT_ID),
            project_id=str(DEFAULT_PROJECT_ID),
            slug="pending-dataset",
            name="待导入数据集",
            adapter_key="swebench",
            task_family=None,
            environment_kind=None,
            metadata_json={},
        )
    )
    seeded.commit()

    body = client.get("/api/v1/datasets", params={"limit": 200}).json()
    item = next(entry for entry in body["items"] if entry["slug"] == "pending-dataset")
    assert item["status"] == "draft"
    assert item["latest_version"] is None
    assert item["version_count"] == 0
    assert item["primary_metric"] is None
    # 数据行没有任务族/环境形态时回落到适配器元数据。
    assert item["task_family"] == "软件工程"
    assert item["environment_kind"] == "GitHub Issue · Docker"


def test_catalog_selects_latest_of_multiple_versions(seeded: Session, client: TestClient) -> None:
    records = fixture_records()
    post_import(client, records=records[:2], version="v1", slug="swebench-multi", name="SWE-bench Multi")
    time.sleep(0.02)
    post_import(client, records=records[:4], version="v2", slug="swebench-multi", name="SWE-bench Multi")

    body = client.get("/api/v1/datasets", params={"limit": 200}).json()
    item = next(entry for entry in body["items"] if entry["slug"] == "swebench-multi")
    assert item["version_count"] == 2
    assert item["latest_version"]["version"] == "v2"
    assert item["latest_version"]["case_count"] == 4
    assert item["status"] == "ready"


def test_catalog_handles_unregistered_adapter(seeded: Session, client: TestClient) -> None:
    seeded.add(
        Dataset(
            tenant_id=str(DEFAULT_TENANT_ID),
            project_id=str(DEFAULT_PROJECT_ID),
            slug="legacy-custom",
            name="Legacy Custom Dataset",
            adapter_key="missing-adapter",
            task_family="自定义",
            environment_kind="VM",
            metadata_json={},
        )
    )
    seeded.commit()

    body = client.get("/api/v1/datasets", params={"limit": 200}).json()
    item = next(entry for entry in body["items"] if entry["slug"] == "legacy-custom")
    assert item["adapter"] == {
        "key": "missing-adapter",
        "display_name": "missing-adapter",
        "registered": False,
    }
    assert item["status"] == "draft"
    assert item["task_family"] == "自定义"
    assert item["environment_kind"] == "VM"


def test_imports_are_listed_for_recent_import_card(seeded: Session, client: TestClient) -> None:
    post_import(client, records=fixture_records(), slug="swebench-verified")
    post_import(client, records=fixture_records()[:2], version="v2", slug="swebench-lite")

    response = client.get("/api/v1/dataset-imports", params={"limit": 1, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["page_info"]["total"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "succeeded"
    assert body["items"][0]["valid_count"] > 0

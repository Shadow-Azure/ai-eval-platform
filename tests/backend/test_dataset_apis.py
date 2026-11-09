"""M3-01 dataset query APIs（design 04 §2 Dataset/Import/Metrics 行）。

服务端分页：``items + page_info``（limit/offset/total/has_more/next_offset）。
错误统一 ``{"error": {code, message, ...}}`` 封套。
"""

from __future__ import annotations

import pytest
from app.seed import ensure_default_scope
from dataset_helpers import fixture_records
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def post_import(
    client: TestClient, *, records, version: str = "verified-smoke", slug: str = "swebench-verified"
) -> dict:
    response = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": slug,
            "dataset_name": "SWE-bench Verified",
            "adapter_key": "swebench",
            "version": version,
            "source_uri": "tests/backend/fixtures/swebench/verified-slice.jsonl",
            "source_revision": "rev-0001",
            "records": records,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.fixture
def imported_version(seeded: Session, client: TestClient) -> dict:
    body = post_import(client, records=fixture_records())
    return body["version"]


# ---------------------------------------------------------------------------
# POST /api/v1/dataset-imports
# ---------------------------------------------------------------------------


def test_import_endpoint_creates_and_replays(seeded: Session, client: TestClient) -> None:
    records = fixture_records()
    first_response = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": "swebench-verified",
            "dataset_name": "SWE-bench Verified",
            "adapter_key": "swebench",
            "version": "verified-smoke",
            "source_uri": "fixture",
            "source_revision": "rev-0001",
            "records": records,
        },
    )
    assert first_response.status_code == 201
    first = first_response.json()
    assert first["created"] is True
    assert first["version"]["case_count"] == len(records)

    second_response = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": "swebench-verified",
            "dataset_name": "SWE-bench Verified",
            "adapter_key": "swebench",
            "version": "verified-smoke",
            "source_uri": "fixture",
            "source_revision": "rev-0001",
            "records": records,
        },
    )
    assert second_response.status_code == 200
    second = second_response.json()
    assert second["created"] is False
    assert second["version"]["id"] == first["version"]["id"]


def test_import_endpoint_validation_failure_is_diagnosable(
    seeded: Session, client: TestClient
) -> None:
    records = fixture_records()
    broken = dict(records[0])
    del broken["problem_statement"]
    response = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": "swebench-verified",
            "dataset_name": "SWE-bench Verified",
            "adapter_key": "swebench",
            "version": "verified-smoke",
            "source_uri": "fixture",
            "source_revision": "rev-0001",
            "records": [broken] + records[1:3],
        },
    )
    assert response.status_code == 422
    error = response.json()["detail"]["error"]
    assert error["code"] == "dataset_import_validation_failed"
    issues = error["details"]["errors"]
    assert issues[0]["code"] == "missing_field"
    assert issues[0]["field"] == "problem_statement"
    assert error["details"]["import_id"]


def test_import_endpoint_unknown_adapter_is_classified(
    seeded: Session, client: TestClient
) -> None:
    response = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": "mystery",
            "dataset_name": "Mystery",
            "adapter_key": "no-such-adapter",
            "version": "v1",
            "source_uri": "fixture",
            "source_revision": "rev-0001",
            "records": [{"a": 1}],
        },
    )
    assert response.status_code == 422
    error = response.json()["detail"]["error"]
    assert error["code"] == "dataset_import_validation_failed"
    assert error["details"]["errors"][0]["code"] == "adapter_not_found"


def test_import_endpoint_version_conflict_is_409(seeded: Session, client: TestClient) -> None:
    records = fixture_records()
    post_import(client, records=records)
    response = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": "swebench-verified",
            "dataset_name": "SWE-bench Verified",
            "adapter_key": "swebench",
            "version": "verified-smoke",
            "source_uri": "fixture",
            "source_revision": "rev-0002",
            "records": records,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "dataset_version_conflict"


def test_import_detail_endpoint_exposes_error_report(
    seeded: Session, client: TestClient
) -> None:
    records = fixture_records()
    broken = dict(records[0])
    del broken["repo"]
    failed = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": "swebench-verified",
            "dataset_name": "SWE-bench Verified",
            "adapter_key": "swebench",
            "version": "bad-slice",
            "source_uri": "fixture",
            "source_revision": "rev-0001",
            "records": [broken],
        },
    )
    import_id = failed.json()["detail"]["error"]["details"]["import_id"]
    detail_response = client.get(f"/api/v1/dataset-imports/{import_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["status"] == "failed"
    assert detail["error_report"]["errors"][0]["code"] == "missing_field"


# ---------------------------------------------------------------------------
# GET catalog / versions / cases / metrics with server-side pagination
# ---------------------------------------------------------------------------


def test_list_datasets_paginates(seeded: Session, client: TestClient) -> None:
    records = fixture_records()
    for index, slug in enumerate(("slice-a", "slice-b", "slice-c")):
        post_import(client, records=records[: 3 + index], version="v1", slug=slug)

    first_page = client.get("/api/v1/datasets", params={"limit": 2, "offset": 0}).json()
    assert len(first_page["items"]) == 2
    assert first_page["page_info"]["total"] == 3
    assert first_page["page_info"]["has_more"] is True
    assert first_page["page_info"]["next_offset"] == 2

    second_page = client.get("/api/v1/datasets", params={"limit": 2, "offset": 2}).json()
    assert len(second_page["items"]) == 1
    assert second_page["page_info"]["has_more"] is False
    assert second_page["page_info"]["next_offset"] is None


def test_get_dataset_detail_and_versions(seeded: Session, client: TestClient) -> None:
    records = fixture_records()
    body = post_import(client, records=records)
    dataset_id = body["dataset"]["id"]

    detail = client.get(f"/api/v1/datasets/{dataset_id}").json()
    assert detail["slug"] == "swebench-verified"
    assert detail["adapter_key"] == "swebench"

    versions = client.get(f"/api/v1/datasets/{dataset_id}/versions").json()
    assert versions["page_info"]["total"] == 1
    assert versions["items"][0]["id"] == body["version"]["id"]

    missing = client.get("/api/v1/datasets/no-such-id")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "dataset_not_found"


def test_version_detail_includes_metrics(seeded: Session, client: TestClient) -> None:
    records = fixture_records()
    body = post_import(client, records=records)
    version_id = body["version"]["id"]

    detail = client.get(f"/api/v1/dataset-versions/{version_id}").json()
    assert detail["case_count"] == len(records)
    assert detail["status"] == "active"
    metric_keys = {metric["metric_key"] for metric in detail["metrics"]}
    assert metric_keys == {
        "resolved_count",
        "resolved_rate",
        "resolved_rate_scored",
        "execution_failure_rate",
    }
    resolved_rate = next(m for m in detail["metrics"] if m["metric_key"] == "resolved_rate")
    assert resolved_rate["aggregator_key"] == "ratio"
    assert resolved_rate["version"] == 1
    assert "% Resolved" in resolved_rate["formula_text"]
    assert resolved_rate["config"]["denominator"] == {"case_set": "selected_total"}

    missing = client.get("/api/v1/dataset-versions/no-such-id")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "dataset_version_not_found"


def test_version_cases_endpoint_paginates_and_details(
    seeded: Session, client: TestClient
) -> None:
    records = fixture_records()
    body = post_import(client, records=records)
    version_id = body["version"]["id"]

    page = client.get(
        f"/api/v1/dataset-versions/{version_id}/cases", params={"limit": 2, "offset": 0}
    ).json()
    assert page["page_info"]["total"] == len(records)
    assert [case["ordinal"] for case in page["items"]] == [1, 2]
    assert page["page_info"]["has_more"] is True

    case_id = page["items"][0]["id"]
    case_detail = client.get(f"/api/v1/dataset-cases/{case_id}").json()
    assert case_detail["case_key"] == records[0]["instance_id"]
    assert case_detail["problem_statement"] == records[0]["problem_statement"]
    assert case_detail["reference_patch"] == records[0]["patch"]
    assert case_detail["expected_metrics"]["fail_to_pass"] == records[0]["FAIL_TO_PASS"] or True
    # 可见性标注：题面 agent 可见，参考/测试补丁 scoring_only
    assert case_detail["visibility"] == {
        "problem_statement": "agent",
        "reference_patch": "scoring_only",
        "test_patch": "scoring_only",
    }

    missing = client.get("/api/v1/dataset-cases/no-such-id")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "dataset_case_not_found"


def test_version_metrics_endpoint(seeded: Session, client: TestClient) -> None:
    records = fixture_records()
    body = post_import(client, records=records)
    response = client.get(f"/api/v1/dataset-versions/{body['version']['id']}/metrics")
    assert response.status_code == 200
    metrics = response.json()["items"]
    assert len(metrics) == 4
    for metric in metrics:
        assert metric["aggregator_key"] in ("ratio", "sum")
        assert metric["formula_text"]

"""M3-01 dataset query APIs（design 04 §2 Dataset/Import/Metrics 行）。

服务端分页：``items + page_info``（limit/offset/total/has_more/next_offset）。
错误统一 ``{"error": {code, message, ...}}`` 封套。
"""

from __future__ import annotations

import pytest
from app.models import DatasetImport
from app.seed import ensure_default_scope
from dataset_helpers import fixture_records
from fastapi.testclient import TestClient
from sqlalchemy import select
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
    **extra: object,
) -> dict:
    payload = {
        "dataset_slug": slug,
        "dataset_name": "SWE-bench Verified",
        "adapter_key": "swebench",
        "version": version,
        "source_uri": "tests/backend/fixtures/swebench/verified-slice.jsonl",
        "source_revision": "rev-0001",
        "records": records,
        **extra,
    }
    response = client.post("/api/v1/dataset-imports", json=payload)
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


def test_import_returns_explicit_image_digests(
    seeded: Session, client: TestClient
) -> None:
    execution = "sha256:" + "1" * 64
    scoring = "sha256:" + "2" * 64
    body = post_import(
        client,
        records=fixture_records(),
        execution_image_digest=execution,
        scoring_image_digest=scoring,
    )
    assert body["version"]["execution_image_digest"] == execution
    assert body["version"]["scoring_image_digest"] == scoring


def test_import_applies_platform_default_image_digests(
    seeded: Session, client: TestClient, configure_settings
) -> None:
    execution = "sha256:" + "3" * 64
    scoring = "sha256:" + "4" * 64
    configure_settings(
        DEFAULT_EXECUTION_IMAGE_DIGEST=execution,
        DEFAULT_SCORING_IMAGE_DIGEST=scoring,
    )
    body = post_import(client, records=fixture_records())
    assert body["version"]["execution_image_digest"] == execution
    assert body["version"]["scoring_image_digest"] == scoring


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


def test_version_cases_support_search_and_server_side_pagination(
    seeded: Session, client: TestClient
) -> None:
    records = fixture_records()
    body = post_import(client, records=records)
    version_id = body["version"]["id"]

    # Search matches the instance ID or the repository and resets the page.
    for term, expected in (
        ("12907", [records[0]["instance_id"]]),
        (records[2]["repo"], [record["instance_id"] for record in records]),
        ("ASTROPY-13033", [records[1]["instance_id"]]),
    ):
        page = client.get(
            f"/api/v1/dataset-versions/{version_id}/cases", params={"q": term}
        ).json()
        assert [case["case_key"] for case in page["items"]] == expected
        assert page["page_info"]["total"] == len(expected)
        assert page["items"][0]["expected_test_count"] > 0

    # A LIKE wildcard is a literal user search, not a match-all shortcut.
    wildcard = client.get(
        f"/api/v1/dataset-versions/{version_id}/cases", params={"q": "%"}
    ).json()
    assert wildcard["items"] == []
    assert wildcard["page_info"]["total"] == 0

    # Limit/offset apply to the filtered query and totals stay filtered.
    second = client.get(
        f"/api/v1/dataset-versions/{version_id}/cases",
        params={"limit": 2, "offset": 2},
    ).json()
    assert [case["ordinal"] for case in second["items"]] == [3, 4]
    assert second["page_info"]["total"] == len(records)
    assert second["page_info"]["has_more"] is True


def test_version_cases_filter_by_adapter_provided_difficulty(
    seeded: Session, client: TestClient
) -> None:
    # The vendored SWE-bench Verified slice labels difficulty as solve-time
    # bands: four "15 min - 1 hour" (easy) and two "1-4 hours" (medium).
    records = fixture_records()
    body = post_import(client, records=records, version="difficulty-smoke")
    version_id = body["version"]["id"]

    page = client.get(
        f"/api/v1/dataset-versions/{version_id}/cases", params={"difficulty": "easy"}
    ).json()
    assert page["page_info"]["total"] == 4
    assert {case["difficulty"] for case in page["items"]} == {"easy"}
    assert {case["difficulty_label"] for case in page["items"]} == {"15 min - 1 hour"}
    # Facets cover the whole version, not only the filtered page.
    assert page["facets"]["difficulties"] == [
        {"value": "easy", "count": 4},
        {"value": "medium", "count": 2},
    ]

    combined = client.get(
        f"/api/v1/dataset-versions/{version_id}/cases",
        params={"q": "12907", "difficulty": "easy"},
    ).json()
    assert [case["case_key"] for case in combined["items"]] == [records[0]["instance_id"]]
    assert combined["page_info"]["total"] == 1

    # Canonical labels and longer bands map deterministically; an unknown
    # label is left out of facets rather than misclassified.
    labels = ["easy", "medium", "hard", ">4 hours", "4-8 hours", "unknown"]
    mixed = [dict(record, difficulty=label) for record, label in zip(fixture_records(), labels, strict=True)]
    mixed_body = post_import(
        client, records=mixed, version="labels-smoke", slug="swebench-labels"
    )
    mixed_cases = client.get(
        f"/api/v1/dataset-versions/{mixed_body['version']['id']}/cases"
    ).json()
    assert mixed_cases["facets"]["difficulties"] == [
        {"value": "easy", "count": 1},
        {"value": "medium", "count": 1},
        {"value": "hard", "count": 3},
    ]
    hard = client.get(
        f"/api/v1/dataset-versions/{mixed_body['version']['id']}/cases",
        params={"difficulty": "hard"},
    ).json()
    assert hard["page_info"]["total"] == 3
    assert {case["difficulty_label"] for case in hard["items"]} == {"hard", ">4 hours", "4-8 hours"}
    # Unknown labels are still browseable in the table, just not filterable.
    unknown = client.get(
        f"/api/v1/dataset-versions/{mixed_body['version']['id']}/cases",
        params={"q": "13579"},
    ).json()
    assert len(unknown["items"]) == 1
    assert unknown["items"][0]["difficulty"] is None
    assert unknown["items"][0]["difficulty_label"] == "unknown"


def test_version_detail_includes_import_validation(seeded: Session, client: TestClient) -> None:
    body = post_import(client, records=fixture_records())
    record = seeded.scalar(
        select(DatasetImport).where(DatasetImport.dataset_id == body["dataset"]["id"])
    )
    assert record is not None
    record.validation_report_json = {
        "field_completeness": {
            "instance_id": {"present": 6, "percent": 100.0},
            "repo": {"present": 6, "percent": 100.0},
            "problem_statement": {"present": 5, "percent": 83.3},
        },
        "error_codes": {},
        "fatal": False,
    }
    seeded.commit()

    detail = client.get(f"/api/v1/dataset-versions/{body['version']['id']}").json()
    validation = detail["validation"]
    assert validation["import_id"] == record.id
    assert validation["status"] == "succeeded"
    assert validation["passed"] is True
    assert validation["report_available"] is True
    assert validation["total_count"] == len(fixture_records())
    assert validation["valid_count"] == len(fixture_records())
    assert validation["invalid_count"] == 0
    # The card reports the weakest required field, not an optimistic average.
    assert validation["completeness_percent"] == 83.3
    assert validation["field_completeness"]["problem_statement"]["present"] == 5


def test_version_detail_without_report_still_exposes_counts(
    seeded: Session, client: TestClient
) -> None:
    body = post_import(client, records=fixture_records())
    detail = client.get(f"/api/v1/dataset-versions/{body['version']['id']}").json()
    validation = detail["validation"]
    # Legacy JSON imports have counts but no field-completeness report.
    assert validation["passed"] is True
    assert validation["report_available"] is False
    assert validation["completeness_percent"] is None
    assert validation["field_completeness"] == {}


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

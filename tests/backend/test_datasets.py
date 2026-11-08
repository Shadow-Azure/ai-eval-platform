from __future__ import annotations

from pathlib import Path

import pytest
from app.config import get_settings
from app.datasets import service
from app.datasets.adapter import AdapterCaseError, get_adapter
from app.models import DatasetCase, DatasetVersion
from app.object_store import LocalObjectStore, ObjectStoreError, normalize_key
from app.seed import ensure_default_scope
from sqlalchemy import select
from sqlalchemy.orm import Session

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "datasets"
SLICE = FIXTURES / "swebench-slice.jsonl"
INVALID = FIXTURES / "swebench-invalid.jsonl"


@pytest.fixture
def dataset_env(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "dataset_source_root", str(FIXTURES))
    monkeypatch.setattr(settings, "object_store_root", str(tmp_path / "objects"))
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture
def default_scope(session: Session):
    ensure_default_scope(session)
    return session


def _create_import(session: Session, dataset, path: Path):
    record = service.create_import(
        session,
        tenant_id=dataset.tenant_id,
        dataset=dataset,
        source={"kind": "local_path", "path": str(path)},
    )
    session.commit()
    return record


def test_swerebench_adapter_maps_and_rejects_rows() -> None:
    adapter = get_adapter("swebench")
    case = adapter.parse(
        {
            "instance_id": "django__django-11019",
            "repo": "django/django",
            "base_commit": "0f1e7d1a2b3c4d5e6f708192a3b4c5d6e7f80912",
            "problem_statement": "Fix order.",
            "patch": "diff --git a/a b/a\n",
            "test_patch": "diff --git a/t b/t\n",
            "FAIL_TO_PASS": "[\"tests/test_forms.py::TestForms::test_x\"]",
            "PASS_TO_PASS": [],
        }
    )
    assert case.case_key == "django__django-11019"
    assert case.fail_to_pass == ("tests/test_forms.py::TestForms::test_x",)
    assert case.pass_to_pass == ()
    bundle = {item.path: item for item in adapter.bundle(case)}
    assert bundle["instruction.md"].visibility == "agent"
    assert bundle["solution/solve.patch"].visibility == "scoring"
    assert bundle["tests/test.patch"].visibility == "scoring"

    with pytest.raises(AdapterCaseError):
        adapter.parse({"instance_id": "bad", "repo": "not-a-repo"})


def test_import_builds_case_bundles_and_metrics(
    default_scope: Session, dataset_env: LocalObjectStore
) -> None:
    session = default_scope
    tenant_id, project_id = _scope_ids(session)
    dataset = service.create_dataset(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        slug="swebench-verified-slice",
        name="SWE-bench Verified slice",
    )
    record = _create_import(session, dataset, SLICE)
    record = service.run_import(session, record.id, dataset_env)

    assert record.status == "completed"
    assert record.valid_count == 3
    assert record.invalid_count == 0
    version = session.get(DatasetVersion, record.dataset_version_id)
    assert version is not None and version.status == "ready"
    assert version.case_count == 3
    assert version.task_manifest_key == f"datasets/{dataset.id}/{version.id}/source-manifest.json"
    assert dataset_env.exists(version.task_manifest_key)

    manifest = dataset_env.read_json(version.task_manifest_key)
    assert manifest["case_count"] == 3
    assert len(manifest["metrics"]) == 7
    assert {case["case_key"] for case in manifest["cases"]} == {
        "django__django-11019",
        "sympy__sympy-20590",
        "pytest-dev__pytest-7432",
    }

    cases = list(
        session.scalars(
            select(DatasetCase)
            .where(DatasetCase.dataset_version_id == version.id)
            .order_by(DatasetCase.ordinal)
        )
    )
    assert [case.case_key for case in cases][0] == "django__django-11019"
    assert cases[0].test_patch_key is not None
    assert dataset_env.exists(cases[0].test_patch_key)

    case_manifest = dataset_env.read_json(
        f"datasets/{dataset.id}/{version.id}/cases/django__django-11019/manifest.json"
    )
    by_path = {item["path"]: item for item in case_manifest["files"]}
    assert by_path["instruction.md"]["visibility"] == "agent"
    assert by_path["solution/solve.patch"]["visibility"] == "scoring"
    assert by_path["tests/test.patch"]["visibility"] == "scoring"
    assert all(len(item["sha256"]) == 64 for item in by_path.values())

    metrics = service.version_metrics(session, version.id)
    assert {metric.metric_key for metric in metrics} >= {
        "resolved_rate",
        "resolved_rate_scored",
        "execution_failure_rate",
    }


def test_import_is_idempotent_for_same_source(
    default_scope: Session, dataset_env: LocalObjectStore
) -> None:
    session = default_scope
    tenant_id, project_id = _scope_ids(session)
    dataset = service.create_dataset(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        slug="swebench",
        name="SWE-bench",
    )
    first = _create_import(session, dataset, SLICE)
    service.run_import(session, first.id, dataset_env)
    version_id = first.dataset_version_id

    second = _create_import(session, dataset, SLICE)
    second = service.run_import(session, second.id, dataset_env)

    assert second.status == "completed"
    assert second.dataset_version_id == version_id
    versions = list(
        session.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id))
    )
    assert len(versions) == 1


def test_import_keeps_valid_rows_and_reports_invalid(
    default_scope: Session, dataset_env: LocalObjectStore
) -> None:
    session = default_scope
    tenant_id, project_id = _scope_ids(session)
    dataset = service.create_dataset(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        slug="mixed",
        name="Mixed",
    )
    record = _create_import(session, dataset, INVALID)
    record = service.run_import(session, record.id, dataset_env)

    assert record.status == "completed"
    assert record.valid_count == 2
    assert record.invalid_count == 3
    assert record.error_report_key is not None
    report = dataset_env.read_json(record.error_report_key)
    assert report["invalid_count"] == 3
    assert {row["code"] for row in report["rows"]} == {
        "invalid_base_commit",
        "invalid_json",
        "missing_base_commit",
    }


def test_import_rejects_source_outside_root(default_scope: Session, dataset_env) -> None:
    session = default_scope
    tenant_id, project_id = _scope_ids(session)
    dataset = service.create_dataset(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        slug="bad-source",
        name="Bad source",
    )
    with pytest.raises(service.DatasetImportError) as error:
        service.create_import(
            session,
            tenant_id=dataset.tenant_id,
            dataset=dataset,
            source={"kind": "local_path", "path": "/etc/passwd"},
        )
    assert error.value.code == "source_outside_root"


def test_api_dataset_import_flow(client, dataset_env) -> None:
    created = client.post(
        "/api/v1/datasets",
        json={"slug": "swebench-api", "name": "SWE-bench API"},
    )
    assert created.status_code == 201
    dataset = created.json()

    imported = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_id": dataset["id"],
            "source": {"kind": "local_path", "path": str(SLICE)},
        },
    )
    assert imported.status_code == 202
    import_id = imported.json()["import"]["id"]

    confirmed = client.post(f"/api/v1/dataset-imports/{import_id}/confirm")
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["import"]["status"] == "completed"
    version_id = body["version"]["id"]

    cases = client.get(
        f"/api/v1/dataset-versions/{version_id}/cases",
        params={"limit": 2, "q": "django"},
    )
    assert cases.status_code == 200
    page = cases.json()
    assert len(page["items"]) == 1
    assert page["items"][0]["case_key"] == "django__django-11019"

    metrics = client.get(f"/api/v1/dataset-versions/{version_id}/metrics")
    assert metrics.status_code == 200
    assert len(metrics.json()["items"]) == 7

    detail = client.get(f"/api/v1/datasets/{dataset['id']}")
    assert detail.status_code == 200
    assert detail.json()["versions"][0]["status"] == "ready"

    missing = client.get("/api/v1/dataset-versions/does-not-exist/cases")
    assert missing.status_code == 404


def test_api_rejects_invalid_source(client, dataset_env) -> None:
    response = client.post(
        "/api/v1/dataset-imports",
        json={
            "slug": "bad",
            "name": "Bad",
            "source": {"kind": "local_path", "path": "/etc/passwd"},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "source_outside_root"


def test_object_store_rejects_escaping_keys(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    with pytest.raises(ObjectStoreError):
        normalize_key("../escape")
    with pytest.raises(ObjectStoreError):
        store.put_bytes("/absolute", b"x")


def _scope_ids(session: Session) -> tuple[str, str]:
    tenant, _, project = ensure_default_scope(session)
    return tenant.id, project.id

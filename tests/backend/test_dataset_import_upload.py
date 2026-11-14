"""M5-15 dataset import upload, isolated validation and immutable commit."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.datasets import get_adapter
from app.models import DatasetCase, DatasetImport, DatasetVersion
from app.seed import ensure_default_scope
from app.services import dataset_imports as import_service
from app.services.dataset_imports import validate_source_file
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

FIXTURE = Path(__file__).parent / "fixtures" / "swebench" / "verified-slice.jsonl"


def fixture_records() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


def jsonl_bytes(records: list[dict]) -> bytes:
    return "\n".join(json.dumps(record) for record in records).encode()


def wait_for_import(client: TestClient, import_id: str, expected: set[str], timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    detail: dict = {}
    while time.monotonic() < deadline:
        detail = client.get(f"/api/v1/dataset-imports/{import_id}").json()
        if detail.get("status") in expected:
            return detail
        time.sleep(0.1)
    raise AssertionError(f"import {import_id} stayed {detail.get('status')!r}, expected {expected}")


def wait_for_empty_dir(directory: Path, timeout: float = 10.0) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = [entry.name for entry in directory.iterdir()]
        if not remaining:
            return []
        time.sleep(0.1)
    return remaining


def upload(
    client: TestClient,
    *,
    content: bytes,
    filename: str = "verified-slice.jsonl",
    version: str = "upload-smoke",
    source_revision: str = "rev-0001",
    **extra: object,
):
    data = {
        "dataset_slug": "swebench-verified",
        "dataset_name": "SWE-bench Verified",
        "adapter_key": "swebench",
        "version": version,
        "source_revision": source_revision,
        **{key: str(value) for key, value in extra.items()},
    }
    return client.post(
        "/api/v1/dataset-imports/uploads",
        data=data,
        files={"file": (filename, content, "application/octet-stream")},
    )


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


@pytest.fixture
def import_dir(tmp_path: Path, configure_settings) -> Path:
    directory = tmp_path / "imports"
    directory.mkdir()
    configure_settings(
        DATASET_IMPORT_DIR=str(directory),
        DATASET_IMPORT_TIMEOUT_SECONDS="120",
        DATASET_IMPORT_MEMORY_LIMIT_MB="1024",
        DATASET_IMPORT_PROGRESS_STEP="2",
    )
    return directory


# ---------------------------------------------------------------------------
# Parser + report
# ---------------------------------------------------------------------------


def test_validation_report_covers_json_array_and_jsonl(tmp_path: Path) -> None:
    records = fixture_records()
    array_file = tmp_path / "records.json"
    array_file.write_text(json.dumps(records))
    jsonl_file = tmp_path / "records.jsonl"
    jsonl_file.write_bytes(jsonl_bytes(records))

    adapter = get_adapter("swebench")
    array_result = validate_source_file(array_file, adapter)
    jsonl_result = validate_source_file(jsonl_file, adapter)

    for result in (array_result, jsonl_result):
        assert result.valid is True
        report = result.report
        assert report["total"] == len(records)
        assert report["valid"] == len(records)
        assert report["invalid"] == 0
        assert report["errors"] == []
        assert report["field_completeness"]["instance_id"]["percent"] == 100.0
        assert report["field_completeness"]["base_commit"]["present"] == len(records)
        assert report["fatal"] is False


def test_validation_report_marks_fatal_json_array_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('[{"instance_id": "x"},')
    result = validate_source_file(path, get_adapter("swebench"))
    assert result.valid is False
    assert result.report["fatal"] is True
    assert result.report["invalid"] == 1
    assert result.report["error_codes"]["invalid_json"] == 1


def test_validation_report_classifies_malformed_and_missing_fields(tmp_path: Path) -> None:
    records = fixture_records()
    broken = dict(records[1])
    del broken["repo"]
    content = (
        json.dumps(records[0])
        + "\n"
        + json.dumps(broken)
        + "\n"
        + "{not json}\n"
        + json.dumps(["not", "an", "object"])
        + "\n"
    )
    path = tmp_path / "bad.jsonl"
    path.write_text(content)
    report = validate_source_file(path, get_adapter("swebench")).report

    assert report["total"] == 4
    assert report["valid"] == 1
    assert report["invalid"] == 3
    assert {"missing_field", "invalid_json"} <= set(report["error_codes"])
    assert report["field_completeness"]["repo"]["present"] == 1
    assert report["errors"][0]["case_index"] is not None


# ---------------------------------------------------------------------------
# Upload → validate → commit
# ---------------------------------------------------------------------------


def test_upload_validates_then_commit_creates_immutable_version(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    response = upload(client, content=jsonl_bytes(records))
    assert response.status_code == 201, response.text
    created = response.json()["import"]
    import_id = created["id"]
    created = wait_for_import(client, import_id, {"validated", "failed"})
    assert created["status"] == "validated"
    assert created["file_name"] == "verified-slice.jsonl"
    assert created["file_size"] == len(jsonl_bytes(records))
    assert created["file_digest"].startswith("sha256:")
    assert created["report"]["valid"] == len(records)
    assert created["report"]["field_completeness"]["problem_statement"]["percent"] == 100.0

    # Validation must not create a version before explicit confirmation.
    assert list(seeded.scalars(select(DatasetVersion))) == []
    assert list(seeded.scalars(select(DatasetCase))) == []

    commit = client.post(f"/api/v1/dataset-imports/{import_id}/commit")
    assert commit.status_code == 202, commit.text
    detail = wait_for_import(client, import_id, {"succeeded", "failed"})
    assert detail["status"] == "succeeded"
    assert detail["version_detail"]["case_count"] == len(records)
    assert detail["dataset"]["slug"] == "swebench-verified"
    assert detail["file_digest"] == created["file_digest"]

    assert wait_for_empty_dir(import_dir) == []
    version = seeded.scalar(select(DatasetVersion))
    cases = list(seeded.scalars(select(DatasetCase).where(DatasetCase.dataset_version_id == version.id)))
    assert {case.case_key for case in cases} == {record["instance_id"] for record in records}
    assert not (import_dir / (detail["file_name"] or "")).exists()


def test_upload_accepts_json_array_and_keeps_dataset_name(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    response = upload(
        client,
        content=json.dumps(records).encode(),
        filename="verified-slice.json",
        version="upload-array",
    )
    assert response.status_code == 201
    created = wait_for_import(client, response.json()["import"]["id"], {"validated", "failed"})
    assert created["status"] == "validated"
    assert created["report"]["mode"] == "json"
    assert created["report"]["total"] == len(records)

    assert client.post(f"/api/v1/dataset-imports/{created['id']}/commit").status_code == 202
    detail = wait_for_import(client, created["id"], {"succeeded", "failed"})
    assert detail["dataset"]["name"] == "SWE-bench Verified"


def test_validation_failure_blocks_version_and_reports_items(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    broken = dict(records[0])
    del broken["problem_statement"]
    response = upload(client, content=jsonl_bytes([broken] + records[1:]))
    assert response.status_code == 201
    created = response.json()["import"]
    created = wait_for_import(client, created["id"], {"validated", "failed"})
    assert created["status"] == "failed"
    assert created["invalid_count"] == 1
    assert created["report"]["errors"][0]["code"] == "missing_field"
    assert created["report"]["errors"][0]["field"] == "problem_statement"
    assert created["report"]["field_completeness"]["problem_statement"]["percent"] < 100

    assert list(seeded.scalars(select(DatasetVersion))) == []
    assert list(seeded.scalars(select(DatasetCase))) == []
    commit = client.post(f"/api/v1/dataset-imports/{created['id']}/commit")
    assert commit.status_code == 409
    assert commit.json()["detail"]["error"]["code"] == "dataset_import_state_conflict"


def test_upload_rejects_empty_json_array(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    response = upload(client, content=b"[]", filename="empty.json")
    assert response.status_code == 201
    created = wait_for_import(client, response.json()["import"]["id"], {"failed"})
    assert created["status"] == "failed"
    assert created["report"]["fatal_code"] == "empty_source"
    assert created["report"]["total"] == 0
    assert list(seeded.scalars(select(DatasetVersion))) == []


def test_upload_rejects_empty_bad_extension_and_oversize(
    seeded: Session, client: TestClient, import_dir: Path, configure_settings
) -> None:
    empty = upload(client, content=b"")
    assert empty.status_code == 422
    assert empty.json()["detail"]["error"]["code"] == "dataset_import_empty_file"

    bad_type = upload(client, content=b"{}", filename="cases.csv")
    assert bad_type.status_code == 422
    assert bad_type.json()["detail"]["error"]["code"] == "unsupported_file_type"

    configure_settings(DATASET_IMPORT_MAX_BYTES="10")
    too_large = upload(client, content=jsonl_bytes(fixture_records()))
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["error"]["code"] == "dataset_import_too_large"


def test_commit_requires_validated_status(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    missing = client.post("/api/v1/dataset-imports/no-such-import/commit")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "dataset_import_not_found"


def test_duplicate_commit_keeps_one_version(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    first = upload(client, content=jsonl_bytes(records), version="upload-dup")
    first_id = first.json()["import"]["id"]
    wait_for_import(client, first_id, {"validated"})
    assert client.post(f"/api/v1/dataset-imports/{first_id}/commit").status_code == 202
    wait_for_import(client, first_id, {"succeeded"})

    second = upload(client, content=jsonl_bytes(records), version="upload-dup")
    second_id = second.json()["import"]["id"]
    wait_for_import(client, second_id, {"validated"})
    assert client.post(f"/api/v1/dataset-imports/{second_id}/commit").status_code == 202
    detail = wait_for_import(client, second_id, {"duplicate", "failed"})
    assert detail["status"] == "duplicate"
    assert len(list(seeded.scalars(select(DatasetVersion)))) == 1


def test_cancel_validated_import_removes_source(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    created = upload(client, content=jsonl_bytes(records)).json()["import"]
    wait_for_import(client, created["id"], {"validated"})

    cancelled = client.post(f"/api/v1/dataset-imports/{created['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/v1/dataset-imports/{created['id']}").json()["status"] == "cancelled"
    assert list(seeded.scalars(select(DatasetVersion))) == []
    assert list(import_dir.iterdir()) == []


def test_duplicate_rejects_different_file_content(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    first = upload(client, content=jsonl_bytes(records), version="upload-digest").json()["import"]
    wait_for_import(client, first["id"], {"validated"})
    assert client.post(f"/api/v1/dataset-imports/{first['id']}/commit").status_code == 202
    wait_for_import(client, first["id"], {"succeeded"})

    changed = [dict(record) for record in records]
    changed[0]["problem_statement"] = changed[0]["problem_statement"] + " (edited)"
    second = upload(client, content=jsonl_bytes(changed), version="upload-digest").json()["import"]
    wait_for_import(client, second["id"], {"validated"})
    assert client.post(f"/api/v1/dataset-imports/{second['id']}/commit").status_code == 202
    detail = wait_for_import(client, second["id"], {"failed", "duplicate"})
    assert detail["status"] == "failed"
    assert detail["report"]["fatal_code"] == "version_conflict"
    assert len(list(seeded.scalars(select(DatasetVersion)))) == 1


def test_upload_strips_traversal_from_filename(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    created = upload(
        client, content=jsonl_bytes(records), filename="../../etc/passwd.jsonl", version="upload-name"
    ).json()["import"]
    assert created["file_name"] == "passwd.jsonl"
    assert all(path.parent == import_dir for path in import_dir.iterdir())
    wait_for_import(client, created["id"], {"validated"})
    assert list(import_dir.iterdir())
    assert list(import_dir.glob("*.jsonl"))


def test_orphan_staged_file_is_swept(
    seeded: Session, session: Session, import_dir: Path, configure_settings
) -> None:
    old = datetime.now(UTC) - timedelta(hours=5)
    orphan = import_dir / "orphan.jsonl"
    orphan.write_text("{}\n")
    os.utime(orphan, (old.timestamp(), old.timestamp()))
    configure_settings(DATASET_IMPORT_STALE_SECONDS="60")

    assert import_service.sweep_orphan_files(session) == 1
    assert not orphan.exists()


def test_commit_refuses_source_changed_after_validation(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    created = upload(client, content=jsonl_bytes(records)).json()["import"]
    wait_for_import(client, created["id"], {"validated"})
    staged = next(import_dir.iterdir())
    original = staged.read_bytes()
    staged.write_bytes(original + b"\n")

    assert client.post(f"/api/v1/dataset-imports/{created['id']}/commit").status_code == 202
    detail = wait_for_import(client, created["id"], {"succeeded", "failed"})
    assert detail["status"] == "failed"
    assert detail["report"]["fatal_code"] == "source_changed"
    assert list(seeded.scalars(select(DatasetVersion))) == []


def test_worker_timeout_marks_import_failed(
    seeded: Session, session: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    created = upload(client, content=jsonl_bytes(records)).json()["import"]
    wait_for_import(client, created["id"], {"validated"})
    # Re-open a queued state to simulate a worker that never reports back.
    record = session.get(DatasetImport, created["id"])
    record.status = "queued"
    record.status_updated_at = datetime.now(UTC)
    session.commit()
    database_url = session.get_bind().url.render_as_string(hide_password=False)

    import_service.run_import_job(
        created["id"], "validate", database_url=database_url, timeout_seconds=0.001
    )
    session.expire_all()
    refreshed = session.get(DatasetImport, created["id"])
    assert refreshed.status == "failed"
    assert refreshed.validation_report_json["fatal_code"] == "worker_timeout"
    assert list(session.scalars(select(DatasetVersion))) == []


def test_cancel_wins_before_worker_claim(
    seeded: Session, session: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    created = upload(client, content=jsonl_bytes(records)).json()["import"]
    wait_for_import(client, created["id"], {"validated"})
    record = session.get(DatasetImport, created["id"])
    record.status = "queued"
    session.commit()

    assert client.post(f"/api/v1/dataset-imports/{created['id']}/cancel").status_code == 200
    session.expire_all()
    record = session.get(DatasetImport, created["id"])
    # A worker that was already scheduled must not revive a cancelled import.
    assert import_service.begin_validation(session, record) is False
    session.expire_all()
    assert session.get(DatasetImport, created["id"]).status == "cancelled"


def test_conflicting_version_commit_reports_conflict(
    seeded: Session, client: TestClient, import_dir: Path
) -> None:
    records = fixture_records()
    first = upload(client, content=jsonl_bytes(records), version="upload-conflict").json()["import"]
    wait_for_import(client, first["id"], {"validated"})
    assert client.post(f"/api/v1/dataset-imports/{first['id']}/commit").status_code == 202
    wait_for_import(client, first["id"], {"succeeded"})

    second = upload(
        client, content=jsonl_bytes(records), version="upload-conflict", source_revision="rev-other"
    ).json()["import"]
    wait_for_import(client, second["id"], {"validated"})
    assert client.post(f"/api/v1/dataset-imports/{second['id']}/commit").status_code == 202
    detail = wait_for_import(client, second["id"], {"failed", "duplicate"})
    assert detail["status"] == "failed"
    assert detail["report"]["fatal_code"] == "version_conflict"
    assert len(list(seeded.scalars(select(DatasetVersion)))) == 1


def test_stale_reaper_fails_interrupted_import(
    seeded: Session, session: Session, client: TestClient, import_dir: Path, configure_settings
) -> None:
    records = fixture_records()
    created = upload(client, content=jsonl_bytes(records)).json()["import"]
    wait_for_import(client, created["id"], {"validated"})
    record = session.get(DatasetImport, created["id"])
    record.status = "validating"
    record.status_updated_at = datetime.now(UTC) - timedelta(hours=5)
    session.commit()
    configure_settings(DATASET_IMPORT_STALE_SECONDS="60")

    assert import_service.reap_stale_imports(session) == 1
    session.expire_all()
    refreshed = session.get(DatasetImport, created["id"])
    assert refreshed.status == "failed"
    assert refreshed.validation_report_json["fatal_code"] == "worker_interrupted"
    assert list(import_dir.iterdir()) == []


def test_reaper_expires_abandoned_validated_import(
    seeded: Session, session: Session, client: TestClient, import_dir: Path, configure_settings
) -> None:
    records = fixture_records()
    created = upload(client, content=jsonl_bytes(records)).json()["import"]
    wait_for_import(client, created["id"], {"validated"})
    record = session.get(DatasetImport, created["id"])
    record.status_updated_at = datetime.now(UTC) - timedelta(hours=5)
    session.commit()
    configure_settings(DATASET_IMPORT_STALE_SECONDS="60")

    assert import_service.reap_stale_imports(session) == 1
    session.expire_all()
    refreshed = session.get(DatasetImport, created["id"])
    assert refreshed.status == "failed"
    assert refreshed.validation_report_json["fatal_code"] == "import_expired"
    assert list(import_dir.iterdir()) == []
    assert list(session.scalars(select(DatasetVersion))) == []


def test_begin_commit_blocks_when_image_check_failed(seeded: Session) -> None:
    from app.defaults import DEFAULT_TENANT_ID

    record = DatasetImport(
        tenant_id=str(DEFAULT_TENANT_ID),
        dataset_slug="swebench-verified",
        dataset_name="SWE-bench Verified",
        adapter_key="swebench",
        version="blocked-image-commit",
        source_uri="",
        source_revision="rev-0001",
        status="validated",
        total_count=1,
        valid_count=1,
        invalid_count=0,
        processed_count=1,
        created_by="test",
        validation_report_json={"image_check": {"status": "blocked"}},
    )
    seeded.add(record)
    seeded.commit()

    with pytest.raises(import_service.DatasetImageUnavailable):
        import_service.begin_commit(seeded, record.id)

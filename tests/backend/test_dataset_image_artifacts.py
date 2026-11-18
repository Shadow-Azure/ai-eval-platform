"""Issue #290: dataset runtime image preflight, ensure queue and cleanup state.

The backend slice owns the artifact ledger (one row per image reference), the
HEAD-based readiness report, the enqueue/retry surface and the preparation
gate. The Docker pull/save/upload worker is a later slice; these tests pin the
service interface it will claim from (``claim_preparing_artifact``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.config import Settings, get_settings
from app.models import (
    Dataset,
    DatasetCase,
    DatasetImageArtifact,
    DatasetVersion,
    EvalPreparation,
    EvalRun,
)
from app.seed import ensure_default_scope
from app.services import dataset_image_artifacts as artifacts
from app.services.dataset_image_tarballs import tarball_key
from app.services.object_store import ObjectStoreError
from conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
from fastapi.testclient import TestClient
from object_stub import StubObjectStore
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_preparations import prepare_body, seed_launch

IMAGE_A = "registry.example/eval/runtime-a:latest"
IMAGE_B = "registry.example/eval/runtime-b:latest"
DERIVED_IMAGE = "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"


def _settings(prefix: str = "dataset-images/", **overrides) -> Settings:
    values = {
        "dataset_image_tarball_prefix": prefix,
        "dataset_image_registries": "",
    }
    values.update(overrides)
    return Settings(**values)


def _store_settings(store: StubObjectStore, **overrides) -> Settings:
    values = {
        "s3_endpoint": store.endpoint,
        "s3_bucket": store.bucket,
        "s3_access_key": "ak",
        "s3_secret_key": "sk",
        "s3_addressing_style": "path",
        "dataset_image_registries": "",
    }
    values.update(overrides)
    return Settings(**values)


def _configure_stub(configure_settings, store: StubObjectStore) -> None:
    configure_settings(
        S3_ENDPOINT=store.endpoint,
        S3_BUCKET=store.bucket,
        S3_ACCESS_KEY="ak",
        S3_SECRET_KEY="sk",
        S3_ADDRESSING_STYLE="path",
    )


def _seed_dataset(
    session: Session,
    *,
    adapter_key: str = "swebench",
    version_image: str | None = None,
    cases: list[dict],
) -> DatasetVersion:
    tenant, _workspace, project = ensure_default_scope(session)
    suffix = uuid4().hex[:8]
    dataset = Dataset(
        tenant_id=tenant.id,
        project_id=project.id,
        slug=f"images-{suffix}",
        name="Images",
        adapter_key=adapter_key,
    )
    session.add(dataset)
    session.flush()
    version = DatasetVersion(
        tenant_id=tenant.id,
        dataset_id=dataset.id,
        version=f"v-{suffix}",
        source_uri="fixture://swebench",
        source_revision="40-char-commit",
        adapter_version="1.0",
        case_count=len(cases),
        status="active",
        environment_image=version_image,
    )
    session.add(version)
    session.flush()
    for index, case in enumerate(cases):
        session.add(
            DatasetCase(
                tenant_id=tenant.id,
                dataset_version_id=version.id,
                case_key=case["case_key"],
                ordinal=index,
                repo="django/django",
                base_commit="c" * 40,
                problem_statement="fix it",
                extra_json=case.get("extra_json") or {},
                environment_image=case.get("environment_image"),
            )
        )
    session.commit()
    return version


def _set_first_case_image(session: Session, version_id: str, image: str) -> str:
    case = session.scalars(
        select(DatasetCase)
        .where(DatasetCase.dataset_version_id == version_id)
        .order_by(DatasetCase.ordinal)
    ).first()
    assert case is not None
    case.environment_image = image
    session.commit()
    return case.case_key


def _artifact(session: Session, image: str) -> DatasetImageArtifact:
    session.expire_all()
    row = session.scalar(
        select(DatasetImageArtifact).where(DatasetImageArtifact.image_ref == image)
    )
    assert row is not None
    return row


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _add_viewer(session: Session) -> None:
    from app.models import Tenant, Workspace
    from app.services import identity

    tenant = session.scalar(select(Tenant).where(Tenant.slug == "default"))
    assert tenant is not None
    workspace = session.scalar(select(Workspace).where(Workspace.tenant_id == tenant.id))
    assert workspace is not None
    identity.upsert_member(
        session,
        email=TEST_VIEWER_EMAIL,
        password=TEST_VIEWER_PASSWORD,
        tenant=tenant,
        workspace=workspace,
        role=identity.ROLE_VIEWER,
        is_default=True,
    )
    session.commit()


# ---------------------------------------------------------------------------
# Image resolution
# ---------------------------------------------------------------------------


def test_resolve_images_prefers_case_then_version_and_dedupes(session: Session) -> None:
    version = _seed_dataset(
        session,
        version_image="registry.example/eval/base:1",
        cases=[
            {"case_key": "case-a", "environment_image": IMAGE_A},
            {"case_key": "case-b"},
            {"case_key": "case-c"},
        ],
    )
    images = artifacts.resolve_preparation_images(
        session,
        version,
        ["case-a", "case-b", "case-c"],
        _settings(),
    )
    assert images == sorted([IMAGE_A, "registry.example/eval/base:1"])
    # Only the selected case keys participate.
    assert artifacts.resolve_preparation_images(session, version, ["case-b"], _settings()) == [
        "registry.example/eval/base:1"
    ]
    assert artifacts.resolve_preparation_images(session, version, [], _settings()) == []


def test_resolve_images_derives_swebench_instance_image(session: Session) -> None:
    version = _seed_dataset(
        session,
        cases=[
            {
                "case_key": "astropy__astropy-12907",
                "extra_json": {"instance_id": "astropy__astropy-12907"},
            },
            {
                "case_key": "astropy__astropy-13033",
                "extra_json": {"instance_id": "astropy__astropy-12907"},
            },
            {"case_key": "not-an-instance"},
            {"case_key": "explicit", "environment_image": IMAGE_B},
        ],
    )
    images = artifacts.resolve_preparation_images(
        session,
        version,
        ["astropy__astropy-12907", "astropy__astropy-13033", "not-an-instance", "explicit"],
        _settings(),
    )
    assert images == sorted([DERIVED_IMAGE, IMAGE_B])


# ---------------------------------------------------------------------------
# Artifact ledger and readiness
# ---------------------------------------------------------------------------


def test_get_or_create_artifact_is_idempotent(session: Session) -> None:
    settings = _settings(prefix="runtime-images/")
    first = artifacts.get_or_create_artifact(session, IMAGE_A, settings)
    session.commit()
    second = artifacts.get_or_create_artifact(session, IMAGE_A, settings)
    session.commit()

    assert first.id == second.id
    assert first.object_key == artifacts.object_key_for_image(IMAGE_A, settings)
    assert first.object_key.startswith("runtime-images/")
    assert first.object_key.endswith(".tar.gz")
    assert first.status == "missing"
    assert first.local_cleanup == "pending"
    assert first.attempts == 0
    assert session.scalar(select(func.count()).select_from(DatasetImageArtifact)) == 1


def test_readiness_heads_objects_and_orders_non_ready_first(
    session: Session, configure_settings
) -> None:
    with StubObjectStore() as store:
        settings = _store_settings(store)
        store.add(
            tarball_key(IMAGE_A, prefix=settings.dataset_image_tarball_prefix),
            4_194_304,
        )
        result = artifacts.readiness(
            session, [IMAGE_A, IMAGE_B], settings, recheck=True, enqueue=False
        )
        session.commit()

    assert result["ready"] is False
    assert result["total"] == 2
    assert result["ready_count"] == 1
    assert result["missing_count"] == 1
    assert result["preparing_count"] == 0
    assert result["failed_count"] == 0
    assert result["cleanup_failed_count"] == 0
    assert result["truncated"] is False
    assert result["checked_at"].endswith("Z")
    by_image = {item["image"]: item for item in result["images"]}
    assert result["images"][0]["image"] == IMAGE_B
    assert by_image[IMAGE_A]["status"] == "ready"
    assert by_image[IMAGE_A]["size_bytes"] == 4_194_304
    assert by_image[IMAGE_A]["object_key"] == tarball_key(
        IMAGE_A, prefix=settings.dataset_image_tarball_prefix
    )
    assert by_image[IMAGE_A]["cleanup_status"] == "pending"
    assert by_image[IMAGE_A]["attempts"] == 0
    assert by_image[IMAGE_A]["prepared_at"].endswith("Z")
    assert by_image[IMAGE_B]["status"] == "missing"
    assert by_image[IMAGE_B]["size_bytes"] is None
    assert by_image[IMAGE_B]["attempts"] == 0
    assert by_image[IMAGE_B]["prepared_at"] is None
    assert by_image[IMAGE_B]["cleanup_error"] is None

    # local cleanup failure is exposed without a second OSS round trip.
    row = _artifact(session, IMAGE_B)
    row.local_cleanup = "failed"
    row.cleanup_error = "docker image rm failed"
    session.commit()
    again = artifacts.readiness(session, [IMAGE_A, IMAGE_B], settings)
    assert again["cleanup_failed_count"] == 1
    item = next(item for item in again["images"] if item["image"] == IMAGE_B)
    assert item["cleanup_status"] == "failed"
    assert item["cleanup_error"] == "docker image rm failed"


def test_readiness_enqueues_missing_rows_without_duplicates_and_leases_claims(
    session: Session, configure_settings
) -> None:
    with StubObjectStore() as store:
        settings = _store_settings(store)
        first = artifacts.readiness(
            session, [IMAGE_A, IMAGE_B], settings, recheck=True, enqueue=True
        )
        session.commit()
        second = artifacts.readiness(
            session, [IMAGE_A, IMAGE_B], settings, recheck=False, enqueue=True
        )
        session.commit()

        assert first["preparing_count"] == 2
        assert first["ready"] is False
        assert second["preparing_count"] == 2
        assert session.scalar(select(func.count()).select_from(DatasetImageArtifact)) == 2

        before = datetime.now(UTC)
        claimed = artifacts.claim_preparing_artifact(session, settings)
        session.commit()
        assert claimed is not None
        assert claimed.status == "preparing"
        assert claimed.attempts == 1
        assert claimed.next_attempt_at is not None
        lease = claimed.next_attempt_at
        if lease.tzinfo is None:
            lease = lease.replace(tzinfo=UTC)
        assert lease > before
        assert lease <= before + timedelta(
            seconds=settings.dataset_image_prepare_lease_seconds + 5
        )

        # The lease stops the second claim from picking the same row.
        other = artifacts.claim_preparing_artifact(session, settings)
        session.commit()
        assert other is not None
        assert other.id != claimed.id
        assert artifacts.claim_preparing_artifact(session, settings) is None


def test_readiness_tolerates_store_errors_as_failed(session: Session) -> None:
    settings = Settings(
        s3_endpoint="http://127.0.0.1:1",
        s3_access_key="ak",
        s3_secret_key="sk",
        dataset_image_registries="",
    )
    result = artifacts.readiness(session, [IMAGE_A], settings, recheck=True)
    session.commit()
    assert result["ready"] is False
    assert result["failed_count"] == 1
    assert result["images"][0]["status"] == "failed"
    assert result["images"][0]["error"]


def test_readiness_recheck_skips_active_lease_and_backoff(
    session: Session, configure_settings
) -> None:
    with StubObjectStore() as store:
        settings = _store_settings(store)
        lease_until = datetime.now(UTC) + timedelta(minutes=5)
        preparing = artifacts.get_or_create_artifact(session, IMAGE_A, settings)
        preparing.status = "preparing"
        preparing.attempts = 1
        preparing.next_attempt_at = lease_until
        failed = artifacts.get_or_create_artifact(session, IMAGE_B, settings)
        failed.status = "failed"
        failed.attempts = 1
        failed.error = "旧的拉取失败"
        failed.next_attempt_at = datetime.now(UTC) + timedelta(minutes=5)
        session.commit()

        result = artifacts.readiness(
            session, [IMAGE_A, IMAGE_B], settings, recheck=True, enqueue=True
        )
        session.commit()

    # The active worker lease and the failed backoff both win over the recheck:
    # no HEAD ran, so status/lease/attempts are unchanged.
    a = _artifact(session, IMAGE_A)
    b = _artifact(session, IMAGE_B)
    assert a.status == "preparing"
    assert a.attempts == 1
    assert abs((_utc(a.next_attempt_at) - lease_until).total_seconds()) < 1
    assert a.last_checked_at is None
    assert b.status == "failed"
    assert b.error == "旧的拉取失败"
    assert _utc(b.next_attempt_at) > datetime.now(UTC)
    assert b.last_checked_at is None
    assert result["preparing_count"] == 1
    assert result["failed_count"] == 1


def test_readiness_recheck_keeps_ready_row_on_a_store_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings()
    prepared_at = datetime.now(UTC) - timedelta(minutes=5)
    row = artifacts.get_or_create_artifact(session, IMAGE_A, settings)
    row.status = "ready"
    row.size_bytes = 4321
    row.prepared_at = prepared_at
    session.commit()

    class ErrorStore:
        def head_object(self, object_key: str) -> int:
            raise ObjectStoreError("OSS 503")

    monkeypatch.setattr(
        artifacts, "object_store_from_settings", lambda _settings: ErrorStore()
    )
    result = artifacts.readiness(session, [IMAGE_A], settings, recheck=True, enqueue=True)
    session.commit()

    # A transient store error must not flap ready -> missing.
    row = _artifact(session, IMAGE_A)
    assert result["ready"] is True
    assert row.status == "ready"
    assert row.size_bytes == 4321
    assert abs((_utc(row.prepared_at) - prepared_at).total_seconds()) < 1
    assert "OSS 503" in (row.error or "")
    assert row.last_checked_at is not None


def test_readiness_checked_at_prefers_the_newest_row_timestamp(
    session: Session,
) -> None:
    settings = _settings()
    older = datetime.now(UTC) - timedelta(minutes=10)
    newer = datetime.now(UTC) - timedelta(minutes=2)
    first = artifacts.get_or_create_artifact(session, IMAGE_A, settings)
    first.status = "ready"
    first.last_checked_at = older
    second = artifacts.get_or_create_artifact(session, IMAGE_B, settings)
    second.status = "missing"
    second.last_checked_at = newer
    session.commit()

    result = artifacts.readiness(session, [IMAGE_A, IMAGE_B], settings)
    expected = newer.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    assert result["checked_at"] == expected

    # No rows falls back to now rather than crashing on an empty max().
    empty = artifacts.readiness(session, [], settings)
    assert empty["checked_at"].endswith("Z")

    # A recheck reports the check time, not the previous persisted timestamp.
    before = datetime.now(UTC)
    rechecked = artifacts.readiness(session, [IMAGE_A, IMAGE_B], settings, recheck=True)
    checked = datetime.fromisoformat(rechecked["checked_at"].replace("Z", "+00:00"))
    assert checked >= before.replace(microsecond=0) - timedelta(seconds=1)


def test_failed_backoff_gates_requeue_and_stops_after_the_attempt_cap(
    session: Session,
) -> None:
    settings = _settings(dataset_image_prep_max_attempts=2)
    row = artifacts.get_or_create_artifact(session, IMAGE_A, settings)
    row.status = "failed"
    row.attempts = 1
    row.next_attempt_at = datetime.now(UTC) + timedelta(minutes=5)
    session.commit()

    # Future backoff: neither the worker requeue nor the readiness enqueue path
    # may pull the row forward.
    assert artifacts.requeue_due_failed_artifacts(session, settings) == 0
    waiting = artifacts.readiness(session, [IMAGE_A], settings, enqueue=True)
    session.commit()
    assert waiting["failed_count"] == 1
    assert _artifact(session, IMAGE_A).status == "failed"

    # Backoff elapsed: both paths agree it is due.
    row = _artifact(session, IMAGE_A)
    row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    due = artifacts.readiness(session, [IMAGE_A], settings, enqueue=True)
    session.commit()
    assert due["preparing_count"] == 1
    assert _artifact(session, IMAGE_A).status == "preparing"

    # Attempt cap reached: a null next_attempt_at means "terminal", not "due".
    row = _artifact(session, IMAGE_A)
    row.status = "failed"
    row.attempts = settings.dataset_image_prep_max_attempts
    row.next_attempt_at = None
    session.commit()
    assert artifacts.requeue_due_failed_artifacts(session, settings) == 0
    exhausted = artifacts.readiness(session, [IMAGE_A], settings, enqueue=True)
    session.commit()
    assert exhausted["failed_count"] == 1
    assert _artifact(session, IMAGE_A).status == "failed"


def test_readiness_caps_the_image_list_at_200(session: Session) -> None:
    settings = _settings()
    images = [f"registry.example/eval/bulk-{index:03d}:latest" for index in range(201)]
    ready = artifacts.get_or_create_artifact(session, images[-1], settings)
    ready.status = "ready"
    ready.size_bytes = 1
    session.commit()

    result = artifacts.readiness(session, images, settings, recheck=False)
    assert result["total"] == 201
    assert result["ready_count"] == 1
    assert result["missing_count"] == 200
    assert result["truncated"] is True
    assert len(result["images"]) == 200
    assert all(item["status"] == "missing" for item in result["images"])


def test_ensure_and_retry_handle_legacy_snapshots(session: Session) -> None:
    settings = _settings()
    legacy = SimpleNamespace(snapshot_json={"case_count": 2})
    assert artifacts.ensure_preparation_images(session, legacy, settings) is None
    result = artifacts.retry_preparation_images(session, legacy, settings)
    assert result["ready"] is True
    assert result["total"] == 0


def test_retry_resets_only_requested_failed_rows(session: Session) -> None:
    settings = _settings()
    version = _seed_dataset(
        session,
        version_image=IMAGE_A,
        cases=[{"case_key": "case-a"}],
    )
    preparation = EvalPreparation(
        id=str(uuid4()),
        tenant_id=version.tenant_id,
        created_by="admin@test.local",
        status="active",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        snapshot_json={
            "image_refs": [IMAGE_A, IMAGE_B],
            "case_count": 1,
            "dataset": {"id": version.id},
        },
        snapshot_digest="sha256:" + "a" * 64,
    )
    session.add(preparation)
    for image in (IMAGE_A, IMAGE_B):
        row = artifacts.get_or_create_artifact(session, image, settings)
        row.status = "failed"
        row.attempts = 3
        row.error = "pull failed"
        row.next_attempt_at = datetime.now(UTC) - timedelta(minutes=5)
    session.commit()

    result = artifacts.retry_preparation_images(
        session, preparation, settings, images=[IMAGE_A]
    )
    session.commit()
    assert result["preparing_count"] == 1
    assert result["failed_count"] == 1
    a = _artifact(session, IMAGE_A)
    b = _artifact(session, IMAGE_B)
    assert a.status == "preparing"
    assert a.attempts == 0
    assert a.error is None
    assert a.next_attempt_at is not None
    assert b.status == "failed"
    assert b.attempts == 3


# ---------------------------------------------------------------------------
# Preparation integration
# ---------------------------------------------------------------------------


def test_prepare_freezes_image_refs_and_initial_readiness(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    _set_first_case_image(session, ids["dataset_version_id"], IMAGE_A)

    response = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids))
    assert response.status_code == 200, response.text
    body = response.json()
    # Missing images never block snapshot creation.
    assert body["ready"] is True
    assert body["preparation_id"]
    readiness = body["image_readiness"]
    assert readiness["total"] == 1
    assert readiness["ready"] is False
    assert readiness["preparing_count"] == 1
    assert readiness["missing_count"] == 0
    assert readiness["images"][0]["image"] == IMAGE_A
    assert readiness["images"][0]["status"] == "preparing"

    preparation = session.get(EvalPreparation, body["preparation_id"])
    assert preparation is not None
    assert preparation.snapshot_json["image_refs"] == [IMAGE_A]
    assert preparation.snapshot_json["summary"]["image_readiness"]["total"] == 1
    # A fresh prepare enqueues the missing image so the worker starts without
    # waiting for a later create.
    row = _artifact(session, IMAGE_A)
    assert row.status == "preparing"
    assert row.next_attempt_at is not None


def test_blocked_prepare_has_no_image_readiness(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(
            ids, case_selection={"mode": "cases", "case_keys": ["missing-case"]}
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    assert body["image_readiness"] is None


def test_create_blocks_with_409_until_the_tar_is_ready(
    client: TestClient, session: Session, configure_settings
) -> None:
    with StubObjectStore() as store:
        _configure_stub(configure_settings, store)
        ids = seed_launch(session)
        _set_first_case_image(session, ids["dataset_version_id"], IMAGE_A)

        prepared = client.post(
            "/api/v1/evaluations/prepare", json=prepare_body(ids)
        ).json()
        assert prepared["ready"] is True, prepared
        assert prepared["image_readiness"]["ready"] is False
        preparation_id = prepared["preparation_id"]
        headers = {"Idempotency-Key": "idem-image-not-ready"}

        blocked = client.post(
            "/api/v1/evaluations",
            json={"preparation_id": preparation_id},
            headers=headers,
        )
        assert blocked.status_code == 409, blocked.text
        error = blocked.json()["detail"]["error"]
        assert error["code"] == "image_not_ready"
        assert error["message"] == "运行时镜像尚未就绪，请等待准备完成或重试失败镜像"
        assert [item["image"] for item in error["details"]["images"]] == [IMAGE_A]
        assert error["details"]["image_readiness"]["ready"] is False
        # Counts stay authoritative even if the capped image list hides rows.
        assert error["details"]["truncated"] is False
        assert error["details"]["total"] == 1
        assert error["details"]["pending_count"] == 1
        assert error["details"]["counts"] == {
            "ready": 0,
            "preparing": 1,
            "failed": 0,
            "missing": 0,
        }
        assert session.scalar(select(func.count()).select_from(EvalRun)) == 0
        # The 409 still persists the enqueue so the worker can pick it up.
        assert _artifact(session, IMAGE_A).status == "preparing"

        store.add(
            tarball_key(IMAGE_A, prefix=get_settings().dataset_image_tarball_prefix),
            2 * 1024 * 1024,
        )
        created = client.post(
            "/api/v1/evaluations",
            json={"preparation_id": preparation_id},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        ready = _artifact(session, IMAGE_A)
        assert ready.status == "ready"
        assert ready.size_bytes == 2 * 1024 * 1024


def test_create_succeeds_when_every_tar_is_already_ready(
    client: TestClient, session: Session, configure_settings
) -> None:
    with StubObjectStore() as store:
        _configure_stub(configure_settings, store)
        ids = seed_launch(session)
        _set_first_case_image(session, ids["dataset_version_id"], IMAGE_A)
        store.add(
            tarball_key(IMAGE_A, prefix=get_settings().dataset_image_tarball_prefix),
            1024,
        )
        prepared = client.post(
            "/api/v1/evaluations/prepare", json=prepare_body(ids)
        ).json()
        assert prepared["image_readiness"]["ready"] is True
        created = client.post(
            "/api/v1/evaluations",
            json={"preparation_id": prepared["preparation_id"]},
            headers={"Idempotency-Key": "idem-image-ready"},
        )
        assert created.status_code == 201, created.text


# ---------------------------------------------------------------------------
# Artifact API
# ---------------------------------------------------------------------------


def test_artifact_endpoints_report_and_retry(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    _set_first_case_image(session, ids["dataset_version_id"], IMAGE_A)
    prepared = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids)).json()
    preparation_id = prepared["preparation_id"]

    fetched = client.get(
        "/api/v1/dataset-images/artifacts",
        params={"preparation_id": preparation_id},
    )
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["total"] == 1
    assert body["preparing_count"] == 1
    assert body["missing_count"] == 0
    assert body["images"][0]["image"] == IMAGE_A

    ensured = client.post(
        "/api/v1/dataset-images/artifacts/ensure",
        json={"preparation_id": preparation_id, "recheck": False},
    )
    assert ensured.status_code == 200, ensured.text
    assert ensured.json()["preparing_count"] == 1
    queued = _artifact(session, IMAGE_A)
    assert queued.status == "preparing"
    assert queued.next_attempt_at is not None

    queued.status = "failed"
    queued.attempts = 3
    queued.error = "pull timeout"
    session.commit()
    retried = client.post(
        "/api/v1/dataset-images/artifacts/retry",
        json={"preparation_id": preparation_id, "images": [IMAGE_A]},
    )
    assert retried.status_code == 200, retried.text
    retried_body = retried.json()
    assert retried_body["preparing_count"] == 1
    assert retried_body["failed_count"] == 0
    row = _artifact(session, IMAGE_A)
    assert row.status == "preparing"
    assert row.attempts == 0
    assert row.error is None


def test_artifact_retry_is_admin_only(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    _set_first_case_image(session, ids["dataset_version_id"], IMAGE_A)
    prepared = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids)).json()
    preparation_id = prepared["preparation_id"]

    _add_viewer(session)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": TEST_VIEWER_EMAIL, "password": TEST_VIEWER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    forbidden = client.post(
        "/api/v1/dataset-images/artifacts/retry",
        json={"preparation_id": preparation_id, "images": [IMAGE_A]},
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["detail"]["error"]["code"] == "forbidden"


def test_artifact_endpoints_reject_other_users_and_expired_preparations(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    _set_first_case_image(session, ids["dataset_version_id"], IMAGE_A)
    prepared = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids)).json()
    preparation_id = prepared["preparation_id"]

    preparation = session.get(EvalPreparation, preparation_id)
    assert preparation is not None
    preparation.created_by = "someone-else@test.local"
    session.commit()

    for method, path, payload in (
        ("get", "/api/v1/dataset-images/artifacts", None),
        (
            "post",
            "/api/v1/dataset-images/artifacts/ensure",
            {"preparation_id": preparation_id, "recheck": False},
        ),
        (
            "post",
            "/api/v1/dataset-images/artifacts/retry",
            {"preparation_id": preparation_id},
        ),
    ):
        response = (
            client.get(path, params={"preparation_id": preparation_id})
            if method == "get"
            else client.post(path, json=payload)
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["error"]["code"] == "preparation_not_found"

    # A fresh preparation owned by the caller, then expired.
    preparation.created_by = "admin@test.local"
    preparation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    for method, path, payload in (
        ("get", "/api/v1/dataset-images/artifacts", None),
        (
            "post",
            "/api/v1/dataset-images/artifacts/ensure",
            {"preparation_id": preparation_id, "recheck": False},
        ),
        (
            "post",
            "/api/v1/dataset-images/artifacts/retry",
            {"preparation_id": preparation_id},
        ),
    ):
        response = (
            client.get(path, params={"preparation_id": preparation_id})
            if method == "get"
            else client.post(path, json=payload)
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error"]["code"] == "preparation_expired"

    session.expire_all()
    assert session.get(EvalPreparation, preparation_id).status == "expired"


@pytest.mark.parametrize(
    "preparation_id",
    ["does-not-exist"],
)
def test_artifact_endpoints_unknown_preparation_is_404(
    client: TestClient, preparation_id: str
) -> None:
    response = client.get(
        "/api/v1/dataset-images/artifacts",
        params={"preparation_id": preparation_id},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "preparation_not_found"

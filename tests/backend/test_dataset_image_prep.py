"""Issue #290: image prepare worker (pull → save → upload → cleanup).

Docker and object storage are faked (except one end-to-end PUT/HEAD case
through the real streaming ``ObjectStore.put_file`` and the local S3 stub), so
these tests never need a Docker daemon or OSS credentials.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.config import Settings
from app.seed import ensure_default_scope
from app.services import dataset_image_artifacts as artifacts
from app.services import dataset_image_prep as prep
from app.services.dataset_image_prep import DockerCommandError, prepare_one, sweep
from app.services.object_store import ObjectMissing, ObjectStoreError
from app.worker import image_prep as worker
from object_stub import StubObjectStore
from sqlalchemy.orm import Session

IMAGE = "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
MIRROR = "ghcr.io/acme/swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
PAYLOAD = b"compressed-tar-bytes" * 32


class FakeDocker:
    """In-memory ``ImagePreparer`` recording the worker's command sequence."""

    def __init__(
        self,
        *,
        fail_pulls: bool = False,
        fail_pull_refs: set[str] | None = None,
        fail_removes: bool = False,
        export_bytes: bytes = PAYLOAD,
    ) -> None:
        self.fail_pulls = fail_pulls
        self.fail_pull_refs = fail_pull_refs or set()
        self.fail_removes = fail_removes
        self.export_bytes = export_bytes
        self.pull_calls: list[tuple[str, str]] = []
        self.tag_calls: list[tuple[str, str]] = []
        self.export_calls: list[tuple[str, str]] = []
        self.remove_calls: list[str] = []
        self.images: set[str] = set()

    def pull(self, ref: str, platform: str) -> None:
        self.pull_calls.append((ref, platform))
        if self.fail_pulls or ref in self.fail_pull_refs:
            raise DockerCommandError(f"模拟拉取失败：{ref}")
        self.images.add(ref)

    def tag(self, src: str, dst: str) -> None:
        self.tag_calls.append((src, dst))
        self.images.add(dst)

    def export_gzip(self, ref: str, path: str) -> int:
        target = Path(path)
        target.write_bytes(self.export_bytes)
        self.export_calls.append((ref, str(target)))
        return len(self.export_bytes)

    def remove_image(self, ref: str) -> None:
        self.remove_calls.append(ref)
        if self.fail_removes:
            raise DockerCommandError(f"模拟删除失败：{ref}")
        self.images.discard(ref)


class FakeStore:
    """In-memory object store with the two methods the worker uses."""

    def __init__(
        self,
        *,
        objects: dict[str, bytes] | None = None,
        head_size_override: int | None = None,
        fail_put: bool = False,
    ) -> None:
        self.objects = dict(objects or {})
        self.head_size_override = head_size_override
        self.fail_put = fail_put
        self.put_calls: list[tuple[str, int, str]] = []

    def head_object(self, object_key: str) -> int:
        if object_key not in self.objects:
            raise ObjectMissing(object_key)
        if self.head_size_override is not None:
            return self.head_size_override
        return len(self.objects[object_key])

    def put_file(self, object_key, path, *, content_type="application/octet-stream", timeout=1800):
        if self.fail_put:
            raise ObjectStoreError("模拟上传失败")
        data = Path(path).read_bytes()
        self.objects[object_key] = data
        self.put_calls.append((object_key, len(data), content_type))
        return len(data)


def _settings(work_dir: Path, *, sources: str = "docker.io", **overrides) -> Settings:
    values = {
        "dataset_image_prep_work_dir": str(work_dir),
        "dataset_image_prep_min_free_bytes": 0,
        "dataset_image_registries": sources,
        "dataset_image_tarball_prefix": "dataset-images/",
    }
    values.update(overrides)
    return Settings(**values)


def _queue(
    session: Session,
    settings: Settings,
    image: str = IMAGE,
    *,
    attempts: int = 0,
    due_in_seconds: float = -1,
    status: str = "preparing",
) -> None:
    ensure_default_scope(session)
    row = artifacts.get_or_create_artifact(session, image, settings)
    row.status = status
    row.attempts = attempts
    row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=due_in_seconds)
    row.local_cleanup = "pending"
    row.cleanup_error = None
    session.commit()


def _row(session: Session, image: str = IMAGE):
    session.expire_all()
    row = session.query(artifacts.DatasetImageArtifact).filter_by(image_ref=image).one()
    return row


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


def test_prepare_one_pulls_saves_uploads_and_cleans_up(
    session: Session, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    with StubObjectStore() as stub:
        settings = Settings(
            dataset_image_prep_work_dir=str(work_dir),
            dataset_image_prep_min_free_bytes=0,
            dataset_image_registries="docker.io",
            dataset_image_tarball_prefix="dataset-images/",
            s3_endpoint=stub.endpoint,
            s3_bucket=stub.bucket,
            s3_access_key="ak",
            s3_secret_key="sk",
            s3_addressing_style="path",
        )
        _queue(session, settings)
        docker = FakeDocker()

        outcome = prepare_one(session, settings, docker=docker)

        key = artifacts.object_key_for_image(IMAGE, settings)
        row = _row(session)
        assert outcome.claimed is True
        assert outcome.status == "ready"
        assert outcome.already_exists is False
        assert outcome.source == "Docker Hub"
        assert outcome.size_bytes == len(PAYLOAD)
        assert row.status == "ready"
        assert row.attempts == 1
        assert row.size_bytes == len(PAYLOAD)
        assert row.source == "Docker Hub"
        assert row.error is None
        assert row.next_attempt_at is None
        assert row.prepared_at is not None
        assert row.local_cleanup == "done"
        assert row.cleanup_error is None
        # Pull happens once with the configured platform; Docker Hub needs no
        # retag and the upload goes through the real streaming put_file.
        assert docker.pull_calls == [(IMAGE, "linux/amd64")]
        assert docker.tag_calls == []
        assert [ref for ref, _path in docker.export_calls] == [IMAGE]
        exported = Path(docker.export_calls[0][1])
        assert exported.name.startswith("dataset-image-")
        assert exported.name.endswith(".tar.gz")
        assert exported.parent == work_dir
        assert not exported.exists()
        assert stub.known[f"/{stub.bucket}/{key}"] == len(PAYLOAD)
        assert docker.images == set()
        assert list(work_dir.glob("dataset-image-*.tar.gz")) == []


def test_prepare_one_falls_back_to_a_mirror_and_tags_the_canonical_ref(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "work", sources="docker.io,ghcr.io/acme")
    store = FakeStore()
    docker = FakeDocker(fail_pull_refs={IMAGE})
    _queue(session, settings)

    outcome = prepare_one(session, settings, docker=docker, store=store)

    assert outcome.status == "ready"
    assert outcome.source == "ghcr.io/acme"
    assert [ref for ref, _platform in docker.pull_calls] == [IMAGE, MIRROR]
    assert docker.tag_calls == [(MIRROR, IMAGE)]
    assert [ref for ref, _path in docker.export_calls] == [IMAGE]
    row = _row(session)
    assert row.status == "ready"
    assert row.source == "ghcr.io/acme"
    assert row.local_cleanup == "done"
    assert docker.images == set()
    assert store.put_calls and len(store.objects) == 1


def test_prepare_one_marks_ready_without_pulling_when_the_object_exists(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "work", sources="docker.io")
    _queue(session, settings)
    key = artifacts.object_key_for_image(IMAGE, settings)
    store = FakeStore(objects={key: b"0" * 1234})
    docker = FakeDocker()

    outcome = prepare_one(session, settings, docker=docker, store=store)

    row = _row(session)
    assert outcome.status == "ready"
    assert outcome.already_exists is True
    assert outcome.size_bytes == 1234
    assert docker.pull_calls == []
    assert docker.export_calls == []
    assert store.put_calls == []
    assert row.status == "ready"
    assert row.size_bytes == 1234
    assert row.local_cleanup == "done"
    assert row.next_attempt_at is None


def test_prepare_one_uses_the_internal_oss_endpoint(session: Session, tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    with StubObjectStore() as stub:
        settings = Settings(
            dataset_image_prep_work_dir=str(work_dir),
            dataset_image_prep_min_free_bytes=0,
            dataset_image_registries="docker.io",
            dataset_image_tarball_prefix="dataset-images/",
            # The public endpoint is unreachable; only the internal one works,
            # proving the worker did not fall back silently.
            s3_endpoint="http://127.0.0.1:1",
            s3_internal_endpoint=stub.endpoint,
            s3_bucket=stub.bucket,
            s3_access_key="ak",
            s3_secret_key="sk",
            s3_addressing_style="path",
        )
        _queue(session, settings)

        outcome = prepare_one(session, settings, docker=FakeDocker())

        key = artifacts.object_key_for_image(IMAGE, settings)
        assert outcome.status == "ready"
        assert outcome.endpoint == stub.endpoint
        assert stub.known[f"/{stub.bucket}/{key}"] == len(PAYLOAD)


def test_prepare_one_falls_back_to_the_public_endpoint(session: Session, tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    with StubObjectStore() as stub:
        settings = Settings(
            dataset_image_prep_work_dir=str(work_dir),
            dataset_image_prep_min_free_bytes=0,
            dataset_image_registries="docker.io",
            dataset_image_tarball_prefix="dataset-images/",
            s3_endpoint=stub.endpoint,
            s3_internal_endpoint="http://127.0.0.1:1",
            s3_bucket=stub.bucket,
            s3_access_key="ak",
            s3_secret_key="sk",
            s3_addressing_style="path",
        )
        _queue(session, settings)

        outcome = prepare_one(session, settings, docker=FakeDocker())

        assert outcome.status == "ready"
        assert outcome.endpoint == stub.endpoint


# ---------------------------------------------------------------------------
# Failure paths and cleanup
# ---------------------------------------------------------------------------


def test_prepare_one_reports_pull_failures_and_removes_all_local_state(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "work", sources="docker.io,ghcr.io/acme")
    store = FakeStore()
    docker = FakeDocker(fail_pulls=True)
    _queue(session, settings)

    outcome = prepare_one(session, settings, docker=docker, store=store)

    row = _row(session)
    assert outcome.status == "failed"
    assert "所有公开源均拉取失败" in (outcome.error or "")
    assert "Docker Hub" in (outcome.error or "")
    assert "ghcr.io/acme" in (outcome.error or "")
    assert row.status == "failed"
    assert row.attempts == 1
    assert "所有公开源均拉取失败" in (row.error or "")
    # Every attempted source is recorded so the console can explain the failure.
    assert row.source == "Docker Hub, ghcr.io/acme"
    assert _utc(row.next_attempt_at) > datetime.now(UTC)
    assert row.local_cleanup == "done"
    assert row.cleanup_error is None
    assert store.put_calls == []
    assert docker.images == set()
    assert list((tmp_path / "work").glob("dataset-image-*.tar.gz")) == []


def test_prepare_one_refuses_to_pull_when_the_data_disk_is_too_small(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(
        tmp_path / "work",
        dataset_image_prep_min_free_bytes=8 * 1024**3,
    )
    monkeypatch.setattr(prep, "_free_bytes", lambda _path: 1024)
    docker = FakeDocker()
    _queue(session, settings)

    outcome = prepare_one(session, settings, docker=docker, store=FakeStore())

    row = _row(session)
    assert outcome.status == "failed"
    assert "磁盘可用空间不足" in (outcome.error or "")
    assert "请扩容" in (outcome.error or "")
    assert docker.pull_calls == []
    assert row.status == "failed"
    assert row.local_cleanup == "done"
    assert _utc(row.next_attempt_at) > datetime.now(UTC)


def test_prepare_one_fails_clearly_without_object_storage(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "work", s3_endpoint="", s3_access_key="", s3_secret_key="")
    docker = FakeDocker()
    _queue(session, settings)

    outcome = prepare_one(session, settings, docker=docker, store=None)

    assert outcome.status == "failed"
    assert "未配置对象存储" in (outcome.error or "")
    assert docker.pull_calls == []


def test_prepare_one_stays_ready_when_only_local_cleanup_fails(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "work", sources="docker.io")
    store = FakeStore()
    docker = FakeDocker(fail_removes=True)
    _queue(session, settings)

    outcome = prepare_one(session, settings, docker=docker, store=store)

    row = _row(session)
    assert outcome.status == "ready"
    assert row.status == "ready"
    assert row.local_cleanup == "failed"
    assert "模拟删除失败" in (row.cleanup_error or "")
    assert row.error is None
    assert store.put_calls
    # The temp tar is still removed even when docker image removal fails.
    assert list((tmp_path / "work").glob("dataset-image-*.tar.gz")) == []
    # Residue remains for the periodic sweep to retry and is visible on the row.
    assert IMAGE in docker.images


def test_prepare_one_keeps_a_complete_tar_when_upload_head_size_mismatches(
    session: Session, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    settings = _settings(work_dir, sources="docker.io")
    store = FakeStore(head_size_override=len(PAYLOAD) + 1)
    docker = FakeDocker()
    _queue(session, settings)

    outcome = prepare_one(session, settings, docker=docker, store=store)

    row = _row(session)
    tar_path = prep.temp_tarball_path(work_dir, IMAGE)
    assert outcome.status == "failed"
    assert "上传校验失败" in (outcome.error or "")
    assert row.status == "failed"
    # The export is complete; keeping it lets the next attempt retry the upload
    # without another pull. Only a partial export is discarded.
    assert tar_path.exists()
    assert prep.temp_tarball_marker(tar_path).exists()
    assert row.local_cleanup == "pending"
    assert docker.images == set()
    assert store.put_calls


def test_prepare_one_retries_a_failed_upload_from_the_complete_tarball(
    session: Session, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    settings = _settings(work_dir, sources="docker.io")
    _queue(session, settings)
    docker = FakeDocker()

    first = prepare_one(session, settings, docker=docker, store=FakeStore(fail_put=True))

    tar_path = prep.temp_tarball_path(work_dir, IMAGE)
    assert first.status == "failed"
    assert first.reused_tarball is False
    assert tar_path.exists()
    assert prep.complete_tarball_size(tar_path) == len(PAYLOAD)
    assert docker.pull_calls == [(IMAGE, "linux/amd64")]
    assert docker.images == set()

    row = _row(session)
    row.status = "preparing"
    row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    row.claimed_at = None
    row.lease_owner = None
    session.commit()

    retry_docker = FakeDocker()
    second = prepare_one(
        session, settings, docker=retry_docker, store=FakeStore()
    )

    assert second.status == "ready"
    assert second.reused_tarball is True
    assert retry_docker.pull_calls == []
    assert retry_docker.export_calls == []
    assert not tar_path.exists()
    assert not prep.temp_tarball_marker(tar_path).exists()
    row = _row(session)
    assert row.status == "ready"
    assert row.attempts == 2
    assert row.local_cleanup == "done"


def test_startup_sweep_keeps_a_complete_tarball_and_drops_the_partial(
    session: Session, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    settings = _settings(work_dir, sources="docker.io")
    complete = work_dir / "dataset-image-complete.tar.gz"
    complete.write_bytes(PAYLOAD)
    prep._mark_tarball_complete(complete, len(PAYLOAD))
    partial = work_dir / "dataset-image-partial.tar.gz"
    partial.write_bytes(b"partial")

    result = sweep(session, settings, docker=FakeDocker(), startup=True)

    assert result.tarballs_removed == 1
    assert complete.exists()
    assert prep.complete_tarball_size(complete) == len(PAYLOAD)
    assert not partial.exists()


def test_periodic_sweep_expires_an_old_complete_tarball(
    session: Session, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    settings = _settings(work_dir, sources="docker.io")
    complete = work_dir / "dataset-image-old.tar.gz"
    complete.write_bytes(PAYLOAD)
    prep._mark_tarball_complete(complete, len(PAYLOAD))
    old = (datetime.now(UTC) - timedelta(hours=25)).timestamp()
    os.utime(complete, (old, old))
    os.utime(prep.temp_tarball_marker(complete), (old, old))

    result = sweep(session, settings, docker=FakeDocker())

    assert result.tarballs_removed == 1
    assert not complete.exists()
    assert not prep.temp_tarball_marker(complete).exists()


# ---------------------------------------------------------------------------
# Claim lease and retry/backoff
# ---------------------------------------------------------------------------


def test_prepare_one_skips_a_row_whose_claim_lease_is_still_active(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "work")
    docker = FakeDocker()
    _queue(session, settings, due_in_seconds=300)

    outcome = prepare_one(session, settings, docker=docker, store=FakeStore())

    row = _row(session)
    assert outcome.claimed is False
    assert docker.pull_calls == []
    assert row.status == "preparing"
    assert row.attempts == 0


def test_prepare_one_backs_off_then_stops_retrying_at_max_attempts(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(
        tmp_path / "work",
        sources="docker.io",
        dataset_image_prep_max_attempts=2,
    )
    docker = FakeDocker(fail_pulls=True)
    _queue(session, settings)

    first = prepare_one(session, settings, docker=docker, store=FakeStore())
    row = _row(session)
    first_retry = _utc(row.next_attempt_at)
    assert first.status == "failed"
    assert row.attempts == 1
    assert first_retry is not None
    assert first_retry > datetime.now(UTC)

    # Simulate the retry endpoint requeueing the failed row.
    row.status = "preparing"
    row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()

    second = prepare_one(session, settings, docker=docker, store=FakeStore())
    row = _row(session)
    assert second.status == "failed"
    assert row.attempts == 2
    assert row.next_attempt_at is None


def test_prepare_one_requeues_due_failed_rows_but_not_maxed_or_backed_off_ones(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(
        tmp_path / "work",
        sources="docker.io",
        dataset_image_prep_max_attempts=2,
    )
    docker = FakeDocker(fail_pulls=True)
    _queue(session, settings)

    first = prepare_one(session, settings, docker=docker, store=FakeStore())
    row = _row(session)
    assert first.status == "failed"
    assert row.attempts == 1
    assert _utc(row.next_attempt_at) > datetime.now(UTC)
    pulls_after_first = len(docker.pull_calls)

    # Still inside the backoff window: the worker neither requeues nor pulls.
    early = prepare_one(session, settings, docker=docker, store=FakeStore())
    assert early.claimed is False
    assert _row(session).status == "failed"
    assert len(docker.pull_calls) == pulls_after_first

    # Backoff elapsed: the worker requeues and claims the row automatically.
    row = _row(session)
    row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    second = prepare_one(session, settings, docker=docker, store=FakeStore())
    row = _row(session)
    assert second.claimed is True
    assert row.attempts == 2
    assert row.status == "failed"
    assert row.next_attempt_at is None
    assert len(docker.pull_calls) == pulls_after_first + 1

    # The attempt cap is terminal: a null next_attempt_at must not revive it.
    third = prepare_one(session, settings, docker=docker, store=FakeStore())
    assert third.claimed is False
    assert _row(session).status == "failed"
    assert len(docker.pull_calls) == pulls_after_first + 1


# ---------------------------------------------------------------------------
# Startup/periodic sweep
# ---------------------------------------------------------------------------


def test_sweep_cleans_pending_rows_and_stale_tarballs_only(
    session: Session, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    settings = _settings(work_dir, sources="docker.io")
    _queue(session, settings, image=IMAGE, status="ready")
    failed_image = "registry.example/eval/runtime-b:latest"
    _queue(session, settings, image=failed_image, status="failed")
    failed_row = _row(session, failed_image)
    failed_row.local_cleanup = "failed"
    failed_row.cleanup_error = "old failure"
    done_image = "registry.example/eval/runtime-c:latest"
    _queue(session, settings, image=done_image, status="ready")
    done_row = _row(session, done_image)
    done_row.local_cleanup = "done"
    session.commit()

    stale = work_dir / "dataset-image-stale.tar.gz"
    fresh = work_dir / "dataset-image-fresh.tar.gz"
    stale.write_bytes(b"stale")
    fresh.write_bytes(b"fresh")
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(stale, (old, old))
    docker = FakeDocker()

    result = sweep(session, settings, docker=docker, now=datetime.now(UTC))

    assert result.cleaned == 2
    assert result.cleanup_failed == 0
    assert result.tarballs_removed == 1
    assert not stale.exists()
    assert fresh.exists()
    assert _row(session).local_cleanup == "done"
    assert _row(session, failed_image).local_cleanup == "done"
    assert _row(session, failed_image).cleanup_error is None
    assert IMAGE in docker.remove_calls
    assert failed_image in docker.remove_calls
    assert done_image not in docker.remove_calls
    assert docker.images == set()


def test_sweep_records_cleanup_failure_and_keeps_the_row_retryable(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "work", sources="docker.io")
    _queue(session, settings, status="failed")
    docker = FakeDocker(fail_removes=True)

    result = sweep(session, settings, docker=docker)

    row = _row(session)
    assert result.cleaned == 0
    assert result.cleanup_failed == 1
    assert row.local_cleanup == "failed"
    assert "模拟删除失败" in (row.cleanup_error or "")


def test_startup_sweep_cleans_an_interrupted_preparing_row_and_fresh_tarball(
    session: Session, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    settings = _settings(work_dir, sources="docker.io")
    _queue(session, settings, status="preparing")
    row = _row(session)
    row.next_attempt_at = datetime.now(UTC) - timedelta(minutes=5)
    row.local_cleanup = "pending"
    session.commit()
    # Interrupted pulls leave a fresh tar behind; startup must not wait for the
    # 1h stale cutoff.
    fresh = work_dir / "dataset-image-interrupted.tar.gz"
    fresh.write_bytes(b"partial")
    docker = FakeDocker()
    now = datetime.now(UTC)

    result = sweep(session, settings, docker=docker, now=now, startup=True)

    row = _row(session)
    assert result.tarballs_removed == 1
    assert not fresh.exists()
    assert IMAGE in docker.remove_calls
    assert row.status == "preparing"
    assert row.local_cleanup == "done"
    # The row is immediately re-claimable by the worker.
    assert _utc(row.next_attempt_at) is not None
    assert abs((_utc(row.next_attempt_at) - now).total_seconds()) < 1


def test_startup_sweep_reclaims_an_active_lease_but_periodic_does_not(
    session: Session, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    settings = _settings(work_dir, sources="docker.io")
    _queue(session, settings, status="preparing", due_in_seconds=300)
    lease = _utc(_row(session).next_attempt_at)
    fresh = work_dir / "dataset-image-active.tar.gz"
    fresh.write_bytes(b"in-progress")
    docker = FakeDocker()

    periodic = sweep(session, settings, docker=docker, startup=False)

    assert periodic.cleaned == 0
    assert periodic.tarballs_removed == 0
    assert fresh.exists()
    assert _utc(_row(session).next_attempt_at) == lease

    startup = sweep(session, settings, docker=docker, startup=True)

    row = _row(session)
    assert startup.cleaned == 1
    assert startup.tarballs_removed == 1
    assert not fresh.exists()
    assert row.status == "preparing"
    assert _utc(row.next_attempt_at) < lease


def test_periodic_sweep_cleans_an_expired_preparing_row_but_keeps_its_lease(
    session: Session, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "work", sources="docker.io")
    _queue(session, settings, status="preparing", due_in_seconds=-60)
    lease = _utc(_row(session).next_attempt_at)
    docker = FakeDocker()

    result = sweep(session, settings, docker=docker, startup=False)

    row = _row(session)
    assert result.cleaned == 1
    assert row.status == "preparing"
    assert row.local_cleanup == "done"
    assert _utc(row.next_attempt_at) == lease


def test_sweep_keeps_a_complete_retry_tarball_pending_but_drops_a_ready_one(
    session: Session, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    settings = _settings(work_dir, sources="docker.io")
    _queue(session, settings, image=IMAGE, status="failed")
    failed_row = _row(session)
    failed_row.attempts = 1
    failed_row.local_cleanup = "pending"
    session.commit()
    failed_tar = prep.temp_tarball_path(work_dir, IMAGE)
    failed_tar.write_bytes(PAYLOAD)
    prep._mark_tarball_complete(failed_tar, len(PAYLOAD))

    ready_image = "registry.example/eval/runtime-ready:latest"
    _queue(session, settings, image=ready_image, status="ready")
    ready_row = _row(session, ready_image)
    ready_row.local_cleanup = "pending"
    session.commit()
    ready_tar = prep.temp_tarball_path(work_dir, ready_image)
    ready_tar.write_bytes(PAYLOAD)
    prep._mark_tarball_complete(ready_tar, len(PAYLOAD))

    result = sweep(session, settings, docker=FakeDocker())

    assert result.cleaned == 2
    assert result.tarballs_removed == 1
    # The failed row keeps its complete tar for the next upload attempt, so its
    # cleanup stays pending; a ready row no longer needs its tar.
    assert _row(session).local_cleanup == "pending"
    assert failed_tar.exists()
    assert not ready_tar.exists()
    assert _row(session, ready_image).local_cleanup == "done"


def test_run_sweep_passes_the_startup_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    class DummySession:
        def __enter__(self):
            return object()

        def __exit__(self, *_args) -> bool:
            return False

    monkeypatch.setattr(worker, "SessionLocal", DummySession)
    monkeypatch.setattr(
        worker,
        "sweep",
        lambda _session, _settings, docker=None, startup=False: (
            calls.append(startup)
            or SimpleNamespace(cleaned=0, cleanup_failed=0, tarballs_removed=0)
        ),
    )

    worker.run_sweep(SimpleNamespace(), SimpleNamespace(), startup=True)
    worker.run_sweep(SimpleNamespace(), SimpleNamespace())

    assert calls == [True, False]


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def test_write_heartbeat_updates_the_healthcheck_file(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    worker.write_heartbeat(path)
    assert float(path.read_text(encoding="utf-8")) > 0


def test_heartbeat_thread_keeps_writing_until_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "heartbeat"
    written = threading.Event()
    calls: list[Path] = []

    def fake_write(target: Path) -> None:
        calls.append(target)
        written.set()

    monkeypatch.setattr(worker, "write_heartbeat", fake_write)
    thread, stop = worker.start_heartbeat(path, interval=0.01)
    try:
        assert thread.daemon is True
        assert written.wait(2.0), "heartbeat thread never wrote"
    finally:
        stop.set()
        thread.join(timeout=2.0)

    assert calls and calls[0] == path
    assert not thread.is_alive()


def test_write_db_heartbeat_uses_a_session_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class DummySession:
        def __enter__(self) -> object:
            return "session"

        def __exit__(self, *_args) -> bool:
            return False

    monkeypatch.setattr(worker, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(
        worker,
        "record_image_prep_heartbeat",
        lambda session: calls.append(session),
    )
    worker.write_db_heartbeat()
    assert calls == ["session"]

    def fail(_session: object) -> None:
        raise RuntimeError("database down")

    monkeypatch.setattr(worker, "record_image_prep_heartbeat", fail)
    worker.write_db_heartbeat()
    assert calls == ["session"]


def test_disabled_worker_writes_heartbeat_and_idles_until_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``restart: unless-stopped`` must not restart-loop a disabled worker."""

    heartbeat = tmp_path / "image-prep-heartbeat"
    settings = SimpleNamespace(
        dataset_image_prep_enabled=False,
        dataset_image_prep_heartbeat_path=str(heartbeat),
        dataset_image_prep_poll_seconds=0.01,
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    # signal.signal() only works on the main thread; the test runs main() in a
    # helper thread to be able to stop it.
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(worker, "_STOP", False)
    result: dict[str, int] = {}

    thread = threading.Thread(target=lambda: result.update(code=worker.main()))
    thread.start()
    try:
        deadline = time.monotonic() + 2.0
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert heartbeat.exists(), "disabled worker never wrote a heartbeat"
        first = float(heartbeat.read_text(encoding="utf-8"))
        assert first > 0
        # Keep idling (fresh heartbeat) rather than returning 0 immediately.
        time.sleep(0.05)
        assert float(heartbeat.read_text(encoding="utf-8")) >= first
        assert thread.is_alive()
    finally:
        worker._STOP = True
        thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert result["code"] == 0


# ---------------------------------------------------------------------------
# Default docker CLI implementation
# ---------------------------------------------------------------------------


def test_cli_preparer_pull_passes_platform_and_docker_bin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings(
        dataset_image_prep_docker_bin="/usr/local/bin/docker",
        dataset_image_prep_platform="linux/amd64",
    )

    prep.DockerCliImagePreparer(settings).pull(IMAGE, "linux/amd64")

    assert calls == [["/usr/local/bin/docker", "pull", "--platform", "linux/amd64", IMAGE]]


def test_cli_preparer_treats_an_already_removed_image_as_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Error response from daemon: No such image: swebench/x:latest",
        ),
    )
    settings = Settings(dataset_image_prep_docker_bin="docker")

    # The desired end state is "image absent", so a missing image is success.
    prep.DockerCliImagePreparer(settings).remove_image(IMAGE)

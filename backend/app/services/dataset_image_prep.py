"""M8 (#290): dataset runtime image preparation worker.

The preflight ledger enqueues one row per missing tarball; this module performs
the actual ``pull -> save | gzip -> upload -> HEAD`` pipeline and, equally
important, removes the local image and temporary tarball afterwards.

Isolation: the worker expects a dedicated Docker-in-Docker daemon (compose
profile ``image-prep``). It never talks to the host Docker socket, so
``docker image rm`` cannot delete the fixed runner's cached images. The Docker
data root and ``dataset_image_prep_work_dir`` both live on the data disk; a
shortfall below ``dataset_image_prep_min_free_bytes`` fails before any pull.

Docker Hub pulls intentionally stay on the canonical reference and never use
the runner egress proxy (that proxy only allows GitHub). The compose service
does not receive ``RUNNER_EGRESS_PROXY``.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..defaults import DEFAULT_TENANT_ID
from ..models import DatasetImageArtifact
from .dataset_image_artifacts import claim_preparing_artifact, requeue_due_failed_artifacts
from .dataset_images import RegistrySource, candidate_ref_pairs, effective_sources
from .object_store import ObjectMissing, ObjectStoreError, object_store_from_settings

PULL_TIMEOUT_SECONDS = 1800.0
TAG_TIMEOUT_SECONDS = 120.0
SAVE_TIMEOUT_SECONDS = 1800.0
REMOVE_TIMEOUT_SECONDS = 120.0
UPLOAD_TIMEOUT_SECONDS = 3600.0
# Failed rows become eligible for an automatic retry after an exponential
# backoff; after ``dataset_image_prep_max_attempts`` they stay failed until an
# administrator retries them from the console.
RETRY_BACKOFF_BASE_SECONDS = 60.0
RETRY_BACKOFF_CAP_SECONDS = 3600.0
# Startup/periodic residue sweep: temporary tarballs older than this are
# leftovers from a killed process and are deleted.
STALE_TAR_SECONDS = 3600.0
TARBALL_GLOB = "dataset-image-*.tar.gz"
TARBALL_CONTENT_TYPE = "application/gzip"


class PrepareError(Exception):
    """A preparation attempt failed with a user-facing reason."""


class DockerCommandError(PrepareError):
    """The docker CLI rejected a command or timed out."""


class ImagePreparer(Protocol):
    """The Docker operations the worker needs; tests inject an in-memory fake."""

    def pull(self, ref: str, platform: str) -> None: ...

    def tag(self, src: str, dst: str) -> None: ...

    def export_gzip(self, ref: str, path: str) -> int: ...

    def remove_image(self, ref: str) -> None: ...


class DockerCliImagePreparer:
    """Default ``ImagePreparer`` backed by the docker CLI.

    ``DOCKER_HOST`` is inherited from the environment (the compose service
    points it at the isolated DinD daemon). Every command runs the CLI directly
    as an argument list, never through a shell, and the save stream is gzipped
    incrementally so a multi-GB image is never buffered in memory.
    """

    def __init__(self, settings: Settings) -> None:
        self.binary = (settings.dataset_image_prep_docker_bin or "docker").strip()
        self.platform = (settings.dataset_image_prep_platform or "").strip()

    def _run(self, args: list[str], *, timeout: float, action: str):
        try:
            return subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise DockerCommandError(
                f"未找到 Docker CLI（{self.binary}）：{exc}；镜像准备服务需要 docker 命令"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DockerCommandError(f"{action}超时（{int(timeout)} 秒）") from exc

    def _checked(self, args: list[str], *, timeout: float, action: str):
        result = self._run(args, timeout=timeout, action=action)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise DockerCommandError(f"{action}失败：{detail[:500]}")
        return result

    def pull(self, ref: str, platform: str) -> None:
        args = ["pull"]
        if platform:
            args.extend(["--platform", platform])
        args.append(ref)
        self._checked(args, timeout=PULL_TIMEOUT_SECONDS, action=f"拉取镜像 {ref} ")

    def tag(self, src: str, dst: str) -> None:
        self._checked(
            ["tag", src, dst],
            timeout=TAG_TIMEOUT_SECONDS,
            action=f"标记镜像 {src} -> {dst} ",
        )

    def export_gzip(self, ref: str, path: str) -> int:
        target = Path(path)
        timed_out = threading.Event()
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(
                    [self.binary, "save", ref],
                    stdout=subprocess.PIPE,
                    stderr=errors,
                )
            except FileNotFoundError as exc:
                raise DockerCommandError(
                    f"未找到 Docker CLI（{self.binary}）：{exc}；镜像准备服务需要 docker 命令"
                ) from exc

            def _kill_on_timeout() -> None:
                timed_out.set()
                try:
                    process.kill()
                except OSError:
                    pass

            watchdog = threading.Timer(SAVE_TIMEOUT_SECONDS, _kill_on_timeout)
            watchdog.daemon = True
            watchdog.start()
            try:
                with (
                    target.open("wb") as raw,
                    gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1) as compressed,
                ):
                    assert process.stdout is not None
                    while chunk := process.stdout.read(1024 * 1024):
                        compressed.write(chunk)
                try:
                    returncode = process.wait(timeout=60)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.wait()
                    raise DockerCommandError(
                        f"导出镜像 {ref} 超时（进程未在 {int(SAVE_TIMEOUT_SECONDS)} 秒内退出）"
                    ) from exc
                if timed_out.is_set():
                    raise DockerCommandError(
                        f"导出镜像 {ref} 超时（{int(SAVE_TIMEOUT_SECONDS)} 秒）"
                    )
                if returncode != 0:
                    errors.seek(0)
                    detail = errors.read().decode("utf-8", "replace").strip()
                    raise DockerCommandError(f"导出镜像 {ref} 失败：{detail[:500]}")
                return target.stat().st_size
            finally:
                watchdog.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if process.stdout is not None:
                    process.stdout.close()

    def remove_image(self, ref: str) -> None:
        result = self._run(
            ["image", "rm", "-f", ref],
            timeout=REMOVE_TIMEOUT_SECONDS,
            action=f"删除本地镜像 {ref} ",
        )
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout or "").strip()
        # Removing an image that is already gone is the desired end state.
        lowered = detail.lower()
        if "no such image" in lowered or "not found" in lowered:
            return
        raise DockerCommandError(f"删除本地镜像 {ref} 失败：{detail[:500]}")


@dataclass
class PrepareOutcome:
    """Result of one ``prepare_one`` pass, used for worker logs and tests."""

    claimed: bool
    status: str | None = None
    image: str | None = None
    source: str | None = None
    size_bytes: int | None = None
    already_exists: bool = False
    local_cleanup: str | None = None
    cleanup_error: str | None = None
    error: str | None = None


@dataclass
class SweepOutcome:
    cleaned: int = 0
    cleanup_failed: int = 0
    tarballs_removed: int = 0


def _free_bytes(path: str | os.PathLike[str]) -> int:
    """Free bytes on the filesystem holding ``path`` (the data disk)."""

    return shutil.disk_usage(path).free


def _image_digest(image: str) -> str:
    return hashlib.sha256((image or "").strip().encode("utf-8")).hexdigest()


def temp_tarball_path(work_dir: str | os.PathLike[str], image: str) -> Path:
    """Deterministic temporary tarball path under the worker work dir."""

    return Path(work_dir) / f"dataset-image-{_image_digest(image)}.tar.gz"


def _retry_at(attempts: int, max_attempts: int, now: datetime) -> datetime | None:
    """Exponential backoff while attempts remain, ``None`` after the cap."""

    if attempts >= max_attempts:
        return None
    exponent = max(0, attempts - 1)
    delay = min(RETRY_BACKOFF_CAP_SECONDS, RETRY_BACKOFF_BASE_SECONDS * (2**exponent))
    return now + timedelta(seconds=delay)


def _cleanup(
    docker: ImagePreparer,
    tar_path: Path | None,
    refs: list[str],
) -> tuple[str, str | None]:
    """Best-effort removal of the temp tar and all local image refs.

    Returns ``("done", None)`` or ``("failed", message)``; never raises so a
    cleanup problem cannot turn a successful upload into a failed artifact.
    """

    errors: list[str] = []
    if tar_path is not None:
        try:
            tar_path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"删除临时 tar {tar_path} 失败：{exc}")
    seen: set[str] = set()
    for ref in refs:
        if not ref or ref in seen:
            continue
        seen.add(ref)
        try:
            docker.remove_image(ref)
        except Exception as exc:  # best effort; recorded on the ledger row
            errors.append(f"清理本地镜像 {ref} 失败：{exc}")
    if errors:
        return "failed", "；".join(errors)[:1000]
    return "done", None


def _mark_ready(
    row: DatasetImageArtifact,
    *,
    size: int | None,
    source: str | None,
    now: datetime,
) -> None:
    row.status = "ready"
    row.size_bytes = size
    row.prepared_at = now
    row.error = None
    row.next_attempt_at = None
    if source:
        row.source = source


def prepare_one(
    session: Session,
    settings: Settings,
    docker: ImagePreparer | None = None,
    store=None,
) -> PrepareOutcome:
    """Claim and prepare at most one dataset image tarball.

    The claim lease is committed immediately so the multi-minute pull does not
    hold a database transaction. On success the object is uploaded, HEADed and
    the row marked ``ready`` before local cleanup runs; a cleanup failure keeps
    ``ready`` and records ``local_cleanup=failed`` for the periodic sweep.
    """

    settings = settings or get_settings()
    docker = docker or DockerCliImagePreparer(settings)
    store = store if store is not None else object_store_from_settings(settings)
    # Automatic retry: due failed rows become preparing again, then the normal
    # lease claim below serializes them. The update is one statement, so
    # concurrent workers cannot double-enqueue a row.
    requeue_due_failed_artifacts(session, settings)
    session.commit()
    row = claim_preparing_artifact(session, settings)
    if row is None:
        return PrepareOutcome(claimed=False)

    image = row.image_ref
    object_key = row.object_key
    attempts = row.attempts or 0
    session.commit()

    outcome = PrepareOutcome(claimed=True, image=image)
    work_dir = Path(settings.dataset_image_prep_work_dir)
    tar_path = temp_tarball_path(work_dir, image)
    candidates: list[tuple[RegistrySource, str]] = []
    errors: list[str] = []
    ready = False
    source_name: str | None = None
    attempted_sources: list[str] = []
    size_bytes: int | None = None
    pulled_ref: str | None = None

    try:
        if store is None:
            raise PrepareError(
                "未配置对象存储（S3_ENDPOINT/S3_ACCESS_KEY/S3_SECRET_KEY），无法上传镜像 tar"
            )
        try:
            existing_size = store.head_object(object_key)
        except ObjectMissing:
            existing_size = None
        except ObjectStoreError as exc:
            raise PrepareError(f"检查 OSS 对象 {object_key} 失败：{exc}") from exc

        candidates = candidate_ref_pairs(image, effective_sources(session, settings))
        # ``effective_sources`` opens a read transaction; release it before the
        # multi-minute pull so no database transaction stays open meanwhile.
        session.rollback()

        if existing_size is not None:
            # A previous worker or the manual publish script already uploaded
            # this image; only stale local residue needs attention.
            ready = True
            size_bytes = existing_size
            outcome.already_exists = True
        else:
            try:
                work_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise PrepareError(f"创建镜像准备工作目录 {work_dir} 失败：{exc}") from exc
            try:
                free = _free_bytes(work_dir)
            except OSError as exc:
                raise PrepareError(f"读取数据盘可用空间失败：{exc}") from exc
            minimum = max(0, int(settings.dataset_image_prep_min_free_bytes))
            if free < minimum:
                raise PrepareError(
                    f"磁盘可用空间不足：{work_dir} 所在数据盘仅剩 {free} 字节，"
                    f"低于最低要求 {minimum} 字节；请扩容数据盘后重试"
                )
            if not candidates:
                raise PrepareError(f"没有可用的镜像源，无法拉取 {image}")

            platform = (settings.dataset_image_prep_platform or "").strip()
            pull_errors: list[str] = []
            for source, candidate in candidates:
                if source.name not in attempted_sources:
                    attempted_sources.append(source.name)
                try:
                    docker.pull(candidate, platform)
                except Exception as exc:
                    pull_errors.append(f"{source.name} ({candidate})：{exc}")
                    continue
                pulled_ref = candidate
                source_name = source.name
                break
            if pulled_ref is None:
                raise PrepareError(
                    "所有公开源均拉取失败：" + "；".join(pull_errors)
                )

            canonical = image
            if pulled_ref != canonical:
                docker.tag(pulled_ref, canonical)
            local_size = docker.export_gzip(canonical, str(tar_path))
            if local_size <= 0:
                raise PrepareError("导出的镜像 tar 为空")
            store.put_file(
                object_key,
                tar_path,
                content_type=TARBALL_CONTENT_TYPE,
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
            try:
                remote_size = store.head_object(object_key)
            except ObjectMissing as exc:
                raise PrepareError(f"上传后 HEAD 校验失败：对象 {object_key} 不存在") from exc
            except ObjectStoreError as exc:
                raise PrepareError(f"上传后 HEAD 校验失败：{exc}") from exc
            if remote_size != local_size:
                raise PrepareError(
                    f"上传校验失败：OSS 对象 {object_key} 大小 {remote_size} 字节，"
                    f"本地 tar 大小 {local_size} 字节"
                )
            ready = True
            size_bytes = local_size

        if ready:
            _mark_ready(row, size=size_bytes, source=source_name, now=datetime.now(UTC))
            # Commit the readiness before cleanup: a crash or slow ``docker rmi``
            # must not hide an already-uploaded tarball from the console.
            session.commit()
    except PrepareError as exc:
        errors.append(str(exc))
    except Exception as exc:  # unexpected docker/DB/store failure
        errors.append(f"镜像准备失败：{exc}")
    finally:
        refs = [image, *(candidate for _source, candidate in candidates)]
        cleanup_status, cleanup_error = _cleanup(docker, tar_path, refs)
        if ready:
            row.status = "ready"
            row.error = None
            row.next_attempt_at = None
        else:
            message = "；".join(errors) if errors else "镜像准备失败"
            row.status = "failed"
            row.error = message[:2000]
            # Keep the source that was reached (for example when the pull
            # succeeded but export/upload failed) so the console can diagnose.
            # When every source failed, record the attempted sources instead of
            # leaving a stale/null value next to the full error.
            if source_name:
                row.source = source_name
            elif attempted_sources:
                row.source = ", ".join(attempted_sources)
            row.next_attempt_at = _retry_at(
                attempts,
                max(1, int(settings.dataset_image_prep_max_attempts)),
                datetime.now(UTC),
            )
        row.local_cleanup = cleanup_status
        row.cleanup_error = cleanup_error
        session.commit()
        outcome.status = row.status
        outcome.source = source_name or row.source
        outcome.size_bytes = size_bytes if ready else None
        outcome.local_cleanup = cleanup_status
        outcome.cleanup_error = cleanup_error
        outcome.error = row.error
    return outcome


def sweep(
    session: Session,
    settings: Settings,
    docker: ImagePreparer | None = None,
    *,
    now: datetime | None = None,
    startup: bool = False,
) -> SweepOutcome:
    """Remove local residue for ready/failed rows and stale temporary tarballs.

    This runs at worker startup (to clear what an interrupted process left) and
    periodically. It only touches rows that still owe cleanup and files matching
    the worker's own ``dataset-image-*.tar.gz`` prefix, never platform images.

    ``startup=True`` additionally reclaims every ``preparing`` row (an
    interrupted pull leaves the status behind with a lease nobody owns), drops
    all temporary tarballs regardless of age, and sets ``next_attempt_at=now``
    after cleanup so the worker re-claims the row immediately. Periodic sweeps
    only touch expired leases and keep tarballs younger than
    ``STALE_TAR_SECONDS``.
    """

    settings = settings or get_settings()
    docker = docker or DockerCliImagePreparer(settings)
    now = now or datetime.now(UTC)
    outcome = SweepOutcome()
    lease_due = or_(
        DatasetImageArtifact.next_attempt_at.is_(None),
        DatasetImageArtifact.next_attempt_at <= now,
    )
    interrupted = DatasetImageArtifact.status == "preparing"
    if not startup:
        interrupted = and_(interrupted, lease_due)
    rows = list(
        session.scalars(
            select(DatasetImageArtifact).where(
                DatasetImageArtifact.tenant_id == str(DEFAULT_TENANT_ID),
                or_(
                    and_(
                        DatasetImageArtifact.status.in_(("ready", "failed")),
                        DatasetImageArtifact.local_cleanup != "done",
                    ),
                    interrupted,
                ),
            )
        )
    )
    sources: list[RegistrySource] = []
    if rows:
        try:
            sources = effective_sources(session, settings)
        except Exception:
            sources = []
        for row in rows:
            refs = [row.image_ref]
            try:
                for _source, candidate in candidate_ref_pairs(row.image_ref, sources):
                    if candidate not in refs:
                        refs.append(candidate)
            except Exception:
                pass
            cleanup_status, cleanup_error = _cleanup(docker, None, refs)
            row.local_cleanup = cleanup_status
            row.cleanup_error = cleanup_error
            if cleanup_status == "done":
                outcome.cleaned += 1
            else:
                outcome.cleanup_failed += 1
            if row.status == "preparing":
                # Keep the row claimable. A startup sweep is authoritative (the
                # previous process is gone), so the lease is reset to now; a
                # periodic sweep only cleans an already-expired lease and must
                # not move it.
                if startup:
                    row.next_attempt_at = now
            session.commit()

    work_dir = Path(settings.dataset_image_prep_work_dir)
    if work_dir.is_dir():
        cutoff = now.timestamp() - STALE_TAR_SECONDS
        for path in work_dir.glob(TARBALL_GLOB):
            try:
                if path.is_file() and (startup or path.stat().st_mtime < cutoff):
                    path.unlink()
                    outcome.tarballs_removed += 1
            except OSError:
                continue
    return outcome

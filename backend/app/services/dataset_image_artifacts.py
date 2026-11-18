"""M8 (#290): dataset runtime image tarball preflight and ensure queue.

The runner can only download a case image from OSS when the deterministic
``docker save`` tarball exists. Before this slice a missing object surfaced
only as a runner-side ``image_unavailable`` after the whole 300s download
budget. This module turns that into a pre-launch contract:

- resolve the selected cases to unique image references (case -> version ->
  SWE-bench derived), mirroring ``dataset_images.image_for_case``;
- HEAD the OSS tarball for each reference and persist one
  ``dataset_image_artifact`` row per image;
- enqueue missing/failed/expired-preparing rows for the (future) prepare
  worker, which claims them through ``claim_preparing_artifact``;
- expose a JSON-safe readiness report used by prepare/create and the console
  polling endpoints.

No Docker or OSS write happens here; the worker slice owns pull/save/upload
and the post-upload local cleanup. The ledger only records its outcome so the
console can show "后台本地残留 0".
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..defaults import DEFAULT_TENANT_ID
from ..models import Dataset, DatasetCase, DatasetImageArtifact, EvalPreparation
from .dataset_image_tarballs import tarball_key
from .dataset_images import DEFAULT_ARCH, image_for_case
from .object_store import ObjectMissing, ObjectStoreError, object_store_from_settings

READINESS_IMAGE_LIMIT = 200
# HEAD checks are cheap but a dataset may select hundreds of cases; keep the
# preflight bounded so prepare stays responsive on a slow OSS endpoint.
CHECK_CONCURRENCY = 8
STATUS_ORDER = {"failed": 0, "preparing": 1, "missing": 2, "ready": 3}


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _tenant_id() -> str:
    return str(DEFAULT_TENANT_ID)


def _normalized_images(images) -> list[str]:
    normalized: set[str] = set()
    for image in images or []:
        if image is None:
            continue
        text = str(image).strip()
        if text:
            normalized.add(text)
    return sorted(normalized)


def object_key_for_image(image: str, settings: Settings) -> str:
    """Deterministic OSS object key for one image reference."""

    return tarball_key(image, prefix=settings.dataset_image_tarball_prefix)


def resolve_preparation_images(
    session: Session,
    version,
    case_keys,
    settings: Settings | None = None,
) -> list[str]:
    """Resolve the selected cases to unique, sorted runtime image references.

    Priority per case: ``dataset_case.environment_image`` -> version-level
    ``environment_image`` -> adapter-derived image (SWE-bench instance naming).
    The version's dataset adapter decides whether derivation is possible.
    """

    settings = settings or get_settings()
    keys = [str(key) for key in (case_keys or []) if str(key)]
    if version is None or not keys:
        return []
    tenant_id = _tenant_id()
    cases = list(
        session.scalars(
            select(DatasetCase).where(
                DatasetCase.tenant_id == tenant_id,
                DatasetCase.dataset_version_id == version.id,
                DatasetCase.case_key.in_(keys),
            )
        )
    )
    dataset = session.get(Dataset, version.dataset_id)
    if dataset is not None and dataset.tenant_id != tenant_id:
        dataset = None
    adapter_key = dataset.adapter_key if dataset is not None else ""
    arch = str(getattr(settings, "dataset_image_arch", DEFAULT_ARCH) or DEFAULT_ARCH)
    version_image = (version.environment_image or "").strip()
    images: set[str] = set()
    for case in cases:
        image = (case.environment_image or "").strip()
        if not image:
            image = version_image
        if not image:
            raw = case.extra_json or {"instance_id": case.case_key}
            derived = image_for_case(adapter_key, case.case_key, raw, arch=arch)
            image = (derived or "").strip()
        if image:
            images.add(image)
    return sorted(images)


def _select_artifact(session: Session, image: str) -> DatasetImageArtifact | None:
    return session.scalar(
        select(DatasetImageArtifact).where(
            DatasetImageArtifact.tenant_id == _tenant_id(),
            DatasetImageArtifact.image_ref == image,
        )
    )


def get_or_create_artifact(
    session: Session, image: str, settings: Settings
) -> DatasetImageArtifact:
    """Return the caller-tenant ledger row for ``image``, creating it once.

    Concurrent prepare/create calls race on the unique ``(tenant_id,
    image_ref)`` key; the loser re-reads the winner's row inside the same
    transaction instead of creating a duplicate.
    """

    normalized = (image or "").strip()
    if not normalized:
        raise ValueError("镜像引用不能为空")
    object_key = object_key_for_image(normalized, settings)
    row = _select_artifact(session, normalized)
    if row is not None:
        if row.object_key != object_key:
            row.object_key = object_key
        return row
    row = DatasetImageArtifact(
        tenant_id=_tenant_id(),
        image_ref=normalized,
        object_key=object_key,
        status="missing",
        local_cleanup="pending",
        attempts=0,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = _select_artifact(session, normalized)
        if existing is None:  # pragma: no cover - unique conflict without a row
            raise
        return existing
    return row


def _head_artifact(store, row: DatasetImageArtifact) -> tuple[str, int | None, str | None]:
    """HEAD one object; return ``(status, size, error)`` without raising."""

    try:
        return "ready", store.head_object(row.object_key), None
    except ObjectMissing:
        return "missing", None, None
    except ObjectStoreError as exc:
        return "failed", None, str(exc)
    except Exception as exc:  # never let one bad probe abort the whole report
        return "failed", None, f"对象存储检查失败：{exc}"


def _recheck_blocked(row: DatasetImageArtifact, now: datetime) -> bool:
    """True while a worker lease or retry backoff owns the row.

    A readiness recheck must not steal an active ``preparing`` lease or pull a
    failed row forward before its backoff: those transitions belong to the
    worker (``claim_preparing_artifact`` / ``requeue_due_failed_artifacts``).
    """

    if row.status not in ("preparing", "failed"):
        return False
    next_attempt = _as_utc(row.next_attempt_at)
    return next_attempt is not None and next_attempt > now


def _needs_enqueue(row: DatasetImageArtifact, now: datetime, max_attempts: int) -> bool:
    if row.status == "missing":
        return True
    if row.status == "failed":
        # Backoff is authoritative: a failed row is retried automatically only
        # after its next_attempt_at, and never after the attempt cap (the
        # explicit console retry resets attempts and requeues immediately).
        if (row.attempts or 0) >= max_attempts:
            return False
        next_attempt = _as_utc(row.next_attempt_at)
        return next_attempt is None or next_attempt <= now
    if row.status != "preparing":
        return False
    next_attempt = _as_utc(row.next_attempt_at)
    return next_attempt is None or next_attempt <= now


def readiness(
    session: Session,
    images,
    settings: Settings | None = None,
    *,
    recheck: bool = False,
    enqueue: bool = False,
) -> dict:
    """Return the JSON-safe tarball readiness report for ``images``.

    ``recheck`` HEADs every unique object key (bounded concurrency) and updates
    the ledger, except for rows whose ``preparing`` lease or ``failed`` backoff
    is still active: those belong to the worker and are left untouched. A
    transient store error never downgrades a ``ready`` row (only a clean
    ``ObjectMissing`` does) and is recorded as ``error`` instead.
    ``enqueue`` moves missing/failed/expired-preparing rows to ``preparing``
    with ``next_attempt_at=now`` so the worker can claim them; it never resets
    an active lease. ``checked_at`` is the newest ``last_checked_at`` for a
    read-only snapshot and ``now`` when this call performed a recheck, so the
    console's "last checked" is not just the poll time.
    """

    settings = settings or get_settings()
    now = _now()
    unique = _normalized_images(images)
    rows = [get_or_create_artifact(session, image, settings) for image in unique]

    if recheck and rows:
        store = object_store_from_settings(settings)
        if store is not None:
            checkable = [row for row in rows if not _recheck_blocked(row, now)]
            if checkable:
                with ThreadPoolExecutor(
                    max_workers=max(1, min(CHECK_CONCURRENCY, len(checkable)))
                ) as pool:
                    results = list(
                        pool.map(lambda row: _head_artifact(store, row), checkable)
                    )
                for row, (status, size, error) in zip(checkable, results, strict=True):
                    row.last_checked_at = now
                    if status == "ready":
                        row.status = "ready"
                        row.size_bytes = size
                        if row.prepared_at is None:
                            row.prepared_at = now
                        row.next_attempt_at = None
                        row.error = None
                    elif status == "missing":
                        # Only a clean 404 proves the object is gone.
                        row.status = "missing"
                        row.size_bytes = None
                        row.prepared_at = None
                        row.error = None
                    else:
                        # Store error: keep the last known status (a ready row
                        # must not flap to missing during an OSS outage) and
                        # record the failure for the console.
                        row.error = (error or "对象存储检查失败")[:2000]
                        if row.status != "ready":
                            row.status = "failed"

    if enqueue:
        max_attempts = max(1, int(settings.dataset_image_prep_max_attempts))
        for row in rows:
            if _needs_enqueue(row, now, max_attempts):
                row.status = "preparing"
                row.next_attempt_at = now

    if rows:
        session.flush()

    ordered = sorted(rows, key=lambda row: (STATUS_ORDER.get(row.status, 9), row.image_ref))
    ready_count = sum(1 for row in rows if row.status == "ready")
    preparing_count = sum(1 for row in rows if row.status == "preparing")
    failed_count = sum(1 for row in rows if row.status == "failed")
    missing_count = sum(1 for row in rows if row.status == "missing")
    cleanup_failed_count = sum(1 for row in rows if row.local_cleanup == "failed")
    total = len(rows)
    if recheck:
        checked_at = now
    else:
        timestamps = [
            checked
            for checked in (_as_utc(row.last_checked_at) for row in rows)
            if checked is not None
        ]
        checked_at = max(timestamps) if timestamps else now
    return {
        "ready": total == ready_count,
        "total": total,
        "ready_count": ready_count,
        "preparing_count": preparing_count,
        "failed_count": failed_count,
        "missing_count": missing_count,
        "cleanup_failed_count": cleanup_failed_count,
        "images": [
            {
                "image": row.image_ref,
                "status": row.status,
                "object_key": row.object_key,
                "size_bytes": row.size_bytes,
                "source": row.source,
                "error": row.error,
                # Extra operational fields: the frontend ignores unknown keys,
                # the console can surface attempt count and preparation time.
                "attempts": row.attempts or 0,
                "prepared_at": _iso(row.prepared_at),
                "cleanup_status": row.local_cleanup,
                "cleanup_error": row.cleanup_error,
            }
            for row in ordered[:READINESS_IMAGE_LIMIT]
        ],
        "truncated": total > READINESS_IMAGE_LIMIT,
        "checked_at": _iso(checked_at),
    }


def snapshot_image_refs(preparation: EvalPreparation) -> list[str] | None:
    """Frozen image references, or ``None`` for a pre-#290 snapshot."""

    snapshot = preparation.snapshot_json or {}
    if "image_refs" not in snapshot:
        return None
    return _normalized_images(snapshot.get("image_refs"))


def ensure_preparation_images(
    session: Session,
    preparation: EvalPreparation,
    settings: Settings | None = None,
    *,
    enqueue: bool = True,
) -> dict | None:
    """Re-check and optionally enqueue the images frozen on a preparation.

    Legacy snapshots (created before the ``image_refs`` key existed) return
    ``None``; callers treat that as "no images, ready" and keep the previous
    launch behaviour.
    """

    refs = snapshot_image_refs(preparation)
    if refs is None:
        return None
    return readiness(session, refs, settings, recheck=True, enqueue=enqueue)


def retry_preparation_images(
    session: Session,
    preparation: EvalPreparation,
    settings: Settings | None = None,
    images=None,
) -> dict:
    """Requeue failed/missing rows for another prepare attempt.

    ``images`` narrows the retry to the caller's selection; omitted means every
    non-ready image frozen on the preparation. Attempts and the last error are
    reset so the worker starts a fresh cycle, but local cleanup stays owned by
    the worker (a retry never bypasses it).
    """

    settings = settings or get_settings()
    refs = snapshot_image_refs(preparation) or []
    wanted = set(_normalized_images(images)) if images is not None else None
    selected = [image for image in refs if wanted is None or image in wanted]
    now = _now()
    if selected:
        rows = list(
            session.scalars(
                select(DatasetImageArtifact).where(
                    DatasetImageArtifact.tenant_id == _tenant_id(),
                    DatasetImageArtifact.image_ref.in_(selected),
                )
            )
        )
        for row in rows:
            if row.status not in ("failed", "missing"):
                continue
            row.status = "preparing"
            row.next_attempt_at = now
            row.attempts = 0
            row.error = None
        session.flush()
    return readiness(session, refs, settings, recheck=False, enqueue=False)


def requeue_due_failed_artifacts(
    session: Session,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Atomically move due failed rows back to ``preparing``.

    ``prepare_one`` calls this before claiming so automatic retries are driven
    by the worker itself instead of depending on a console refresh. The update
    applies the same tenant scope and ``next_attempt_at`` backoff as the
    enqueue path and never revives a row that exhausted
    ``dataset_image_prep_max_attempts``; the explicit retry endpoint resets
    ``attempts`` and requeues immediately.
    """

    settings = settings or get_settings()
    now = _now(now)
    max_attempts = max(1, int(settings.dataset_image_prep_max_attempts))
    result = session.execute(
        update(DatasetImageArtifact)
        .where(
            DatasetImageArtifact.tenant_id == _tenant_id(),
            DatasetImageArtifact.status == "failed",
            DatasetImageArtifact.attempts < max_attempts,
            or_(
                DatasetImageArtifact.next_attempt_at.is_(None),
                DatasetImageArtifact.next_attempt_at <= now,
            ),
        )
        .values(status="preparing", next_attempt_at=now)
        # Bulk update: do not let SQLAlchemy evaluate the datetime comparison in
        # Python (SQLite returns naive datetimes and would raise).
        .execution_options(synchronize_session=False)
    )
    session.flush()
    return int(result.rowcount or 0)


def claim_preparing_artifact(
    session: Session,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> DatasetImageArtifact | None:
    """Claim one due ``preparing`` row for the future prepare worker.

    Uses ``FOR UPDATE SKIP LOCKED`` on PostgreSQL so concurrent workers never
    claim the same image; the lease (``next_attempt_at``) makes a crashed claim
    reclaimable and ``attempts`` carries the retry count. Docker work is not
    implemented in this slice.
    """

    settings = settings or get_settings()
    now = _now(now)
    lease = max(1, int(settings.dataset_image_prepare_lease_seconds))
    row = session.scalars(
        select(DatasetImageArtifact)
        .where(
            DatasetImageArtifact.tenant_id == _tenant_id(),
            DatasetImageArtifact.status == "preparing",
            or_(
                DatasetImageArtifact.next_attempt_at.is_(None),
                DatasetImageArtifact.next_attempt_at <= now,
            ),
        )
        .order_by(DatasetImageArtifact.next_attempt_at, DatasetImageArtifact.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if row is None:
        return None
    row.next_attempt_at = now + timedelta(seconds=lease)
    row.attempts = (row.attempts or 0) + 1
    session.flush()
    return row

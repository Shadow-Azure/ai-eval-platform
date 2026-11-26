"""M8 (#436): image preparation worker heartbeat and queue health.

The preparation worker runs in a separate compose service, so the API cannot
inspect a container-local healthcheck file. It records a row in
``service_heartbeat`` (the same table the dispatch worker uses); the readiness
report and the launch gate read that row to distinguish "the worker is consuming
the queue" from "rows are only queued and nobody is running".

The helper also exposes queue depth and the head-of-line wait so the console and
machine callers can graph the exact phase the issue was missing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..defaults import DEFAULT_TENANT_ID
from ..models import DatasetImageArtifact, ServiceHeartbeat

IMAGE_PREP_HEARTBEAT_NAME = "image-prep"


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


def image_prep_queue_counts(session: Session) -> dict[str, int]:
    """Live ledger counts for the readiness/health payloads.

    ``queued`` and ``processing`` split the legacy ``preparing`` state: a row is
    queued until a worker sets ``claimed_at``, and processing while the lease is
    live. ``failed`` is the terminal/backoff bucket.
    """

    tenant = DatasetImageArtifact.tenant_id == _tenant_id()
    status = DatasetImageArtifact.status

    def _count(*conditions) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(DatasetImageArtifact)
                .where(tenant, *conditions)
            )
            or 0
        )

    return {
        "queued": _count(status == "preparing", DatasetImageArtifact.claimed_at.is_(None)),
        "processing": _count(
            status == "preparing", DatasetImageArtifact.claimed_at.is_not(None)
        ),
        "failed": _count(status == "failed"),
    }


def record_image_prep_heartbeat(
    session: Session,
    *,
    enabled: bool = True,
    now: datetime | None = None,
) -> ServiceHeartbeat:
    """Upsert the image-prep liveness row with the current queue depth.

    A disabled worker must not look healthy: the console profile is the deploy
    gate, so recording ``enabled=False`` keeps the launch gate closed while the
    container still idles for its healthcheck.
    """

    beat_at = _now(now)
    detail = {"enabled": bool(enabled), **image_prep_queue_counts(session)}
    beat = session.get(ServiceHeartbeat, IMAGE_PREP_HEARTBEAT_NAME)
    if beat is None:
        beat = ServiceHeartbeat(
            name=IMAGE_PREP_HEARTBEAT_NAME,
            beat_at=beat_at,
            detail_json=detail,
        )
        session.add(beat)
    else:
        beat.beat_at = beat_at
        beat.detail_json = detail
    session.commit()
    return beat


def image_prep_health(
    session: Session,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict:
    """JSON-safe liveness and queue snapshot for readiness/launch responses."""

    settings = settings or get_settings()
    now = _now(now)
    beat = session.get(ServiceHeartbeat, IMAGE_PREP_HEARTBEAT_NAME)
    threshold = max(30, int(settings.dataset_image_prep_heartbeat_timeout_seconds))
    counts = image_prep_queue_counts(session)

    if beat is None:
        age: int | None = None
        online = False
        enabled: bool | None = None
        message = "尚未收到镜像准备服务心跳"
    else:
        beat_at = _as_utc(beat.beat_at)
        age = max(0, int((now - beat_at).total_seconds())) if beat_at else None
        enabled = bool((beat.detail_json or {}).get("enabled", True))
        if not enabled:
            online = False
            message = "镜像准备服务已禁用（DATASET_IMAGE_PREP_ENABLED=false）"
        elif age is not None and age > threshold:
            online = False
            message = f"镜像准备服务心跳已过期 {age} 秒（阈值 {threshold} 秒）"
        else:
            online = True
            message = f"镜像准备服务运行中，最近心跳 {age if age is not None else '?'} 秒前"

    oldest_queued = session.scalar(
        select(func.min(DatasetImageArtifact.next_attempt_at)).where(
            DatasetImageArtifact.tenant_id == _tenant_id(),
            DatasetImageArtifact.status == "preparing",
            DatasetImageArtifact.claimed_at.is_(None),
        )
    )
    oldest_queued_at = _as_utc(oldest_queued)
    queue_head_wait_seconds = (
        max(0, int((now - oldest_queued_at).total_seconds()))
        if oldest_queued_at is not None
        else 0
    )

    return {
        "online": online,
        "enabled": enabled,
        "last_heartbeat_at": _iso(beat.beat_at if beat is not None else None),
        "age_seconds": age,
        "timeout_seconds": threshold,
        "queued_count": counts["queued"],
        "processing_count": counts["processing"],
        "failed_count": counts["failed"],
        # Head-of-line wait is measured from the queued row's due timestamp; it
        # tells an operator whether a long tail is real work or a dead queue.
        "queue_head_wait_seconds": queue_head_wait_seconds,
        "message": message,
    }

"""M8 (#436): image preparation snapshot TTL.

A preparation that still needs images must live at least as long as the serial
prepare queue. A fixed one-hour window was shorter than the public-endpoint
queue observed in #436, so the image window is derived from the number of frozen
image references:

    max(eval_preparation_image_ttl_seconds,
        per_image_seconds * image_count)

with a wall-clock cap (``eval_preparation_image_ttl_max_seconds``). The ensure
and retry paths renew an active preparation while its images are still making
progress, so a snapshot cannot expire underneath a healthy long-running queue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..config import Settings
from ..models import EvalPreparation


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


def image_preparation_ttl_seconds(settings: Settings, image_count: int) -> int:
    """TTL for a preparation whose images are not all ready yet."""

    count = max(1, int(image_count or 0))
    floor = max(0, int(settings.eval_preparation_image_ttl_seconds))
    per_image = max(0, int(settings.eval_preparation_image_ttl_per_image_seconds))
    cap = max(floor, int(settings.eval_preparation_image_ttl_max_seconds))
    return min(cap, max(floor, per_image * count))


def renew_image_preparation_ttl(
    preparation: EvalPreparation,
    settings: Settings,
    *,
    image_count: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Extend an active preparation forward; returns True when it changed.

    Only the active status is renewed: consumed/expired snapshots are terminal.
    The snapshot copy of ``expires_at`` is updated with the row so both the
    console response and the persisted snapshot agree. The snapshot digest is
    computed from a separate identity object, so this informational field does
    not invalidate ``consume_preparation``.
    """

    if preparation.status != "active":
        return False
    refs = (preparation.snapshot_json or {}).get("image_refs") or []
    count = image_count if image_count is not None else len(refs)
    now = now or datetime.now(UTC)
    target = now + timedelta(seconds=image_preparation_ttl_seconds(settings, count))
    current = _as_utc(preparation.expires_at)
    if current is not None and current >= target:
        return False
    preparation.expires_at = target
    snapshot = dict(preparation.snapshot_json or {})
    snapshot["expires_at"] = _iso(target)
    preparation.snapshot_json = snapshot
    return True


def renew_image_preparation_if_progressing(
    preparation: EvalPreparation,
    settings: Settings,
    readiness: dict,
    *,
    now: datetime | None = None,
) -> bool:
    """Renew only when the worker is online and the queue is making progress.

    An offline worker or an all-failed queue must not keep an otherwise dead
    snapshot alive; the caller's explicit retry or a new prepare restarts it.
    """

    if readiness.get("ready"):
        return False
    if not bool((readiness.get("worker") or {}).get("online")):
        return False
    pending = int(readiness.get("queued_count") or 0) + int(
        readiness.get("processing_count") or 0
    )
    if pending <= 0:
        return False
    return renew_image_preparation_ttl(preparation, settings, now=now)

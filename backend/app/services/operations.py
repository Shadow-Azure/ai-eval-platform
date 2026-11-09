"""Shared operational-status helpers (M3-06).

The dispatch worker records a heartbeat on every poll; the administrator
operations API reads it to report worker liveness together with the other
service and resource checks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..models import ServiceHeartbeat

WORKER_HEARTBEAT_NAME = "worker"


def record_worker_heartbeat(
    session: Session,
    *,
    pending_outbox: int,
    now: datetime | None = None,
) -> ServiceHeartbeat:
    """Upsert the worker's last-seen timestamp and queue depth."""

    beat_at = now or datetime.now(UTC)
    beat = session.get(ServiceHeartbeat, WORKER_HEARTBEAT_NAME)
    if beat is None:
        beat = ServiceHeartbeat(
            name=WORKER_HEARTBEAT_NAME,
            beat_at=beat_at,
            detail_json={"pending_outbox": pending_outbox},
        )
        session.add(beat)
    else:
        beat.beat_at = beat_at
        beat.detail_json = {"pending_outbox": pending_outbox}
    session.commit()
    return beat

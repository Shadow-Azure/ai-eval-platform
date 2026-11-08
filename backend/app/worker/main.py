"""Minimal worker loop for M2-01.

The dispatcher and ingestion workers arrive with M2-05; this process only proves
that a second long-running container shares the database and can be health-checked.
"""

from __future__ import annotations

import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType

from sqlalchemy import func, select, update

from ..config import get_settings
from ..db import SessionLocal
from ..models import Outbox, Runner

_STOP = False


def _handle_signal(_signum: int, _frame: FrameType | None) -> None:
    global _STOP
    _STOP = True


def mark_stale_runners(session, stale_seconds: int) -> int:
    threshold = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    result = session.execute(
        update(Runner)
        .where(
            Runner.status.in_(("online", "busy")),
            Runner.last_heartbeat_at < threshold,
        )
        .values(status="offline", busy_case_attempt_id=None)
    )
    session.commit()
    return int(result.rowcount or 0)


def main() -> int:
    settings = get_settings()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    heartbeat = Path(settings.worker_heartbeat_path)
    heartbeat.parent.mkdir(parents=True, exist_ok=True)

    while not _STOP:
        try:
            with SessionLocal() as session:
                pending = int(
                    session.scalar(
                        select(func.count())
                        .select_from(Outbox)
                        .where(Outbox.status == "pending")
                    )
                    or 0
                )
                stale = mark_stale_runners(
                    session, settings.runner_heartbeat_stale_seconds
                )
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: database check failed: {exc}", flush=True)
            pending = -1
            stale = -1
        heartbeat.write_text(str(time.time()), encoding="utf-8")
        print(
            f"worker: heartbeat pending_outbox={pending} stale_runners={stale}",
            flush=True,
        )
        time.sleep(settings.worker_poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

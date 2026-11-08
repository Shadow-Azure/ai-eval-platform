"""Minimal worker loop for M2-01.

The dispatcher and ingestion workers arrive with M2-05; this process also runs
the M2-04 runner offline sweep each poll so stale fixed runners flip to
offline even when nobody queries the API.
"""

from __future__ import annotations

import signal
import time
from pathlib import Path
from types import FrameType

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import Outbox
from ..services.runners import sweep_stale_runners

_STOP = False


def sweep_once(session: Session) -> int:
    """One runner offline sweep with the configured heartbeat timeout."""

    settings = get_settings()
    return sweep_stale_runners(session, timeout_seconds=settings.runner_heartbeat_timeout_seconds)


def _handle_signal(_signum: int, _frame: FrameType | None) -> None:
    global _STOP
    _STOP = True


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
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: database check failed: {exc}", flush=True)
            pending = -1
        try:
            with SessionLocal() as session:
                swept = sweep_once(session)
            if swept:
                print(f"worker: swept {swept} stale runner(s) offline", flush=True)
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: runner sweep failed: {exc}", flush=True)
        heartbeat.write_text(str(time.time()), encoding="utf-8")
        print(f"worker: heartbeat pending_outbox={pending}", flush=True)
        time.sleep(settings.worker_poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

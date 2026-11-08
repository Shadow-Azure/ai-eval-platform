"""Minimal worker loop for M2-01.

The dispatcher and ingestion workers arrive with M2-05; this process only proves
that a second long-running container shares the database and can be health-checked.
"""

from __future__ import annotations

import signal
import time
from pathlib import Path
from types import FrameType

from sqlalchemy import func, select

from ..config import get_settings
from ..db import SessionLocal
from ..models import Outbox

_STOP = False


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
        heartbeat.write_text(str(time.time()), encoding="utf-8")
        print(f"worker: heartbeat pending_outbox={pending}", flush=True)
        time.sleep(settings.worker_poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

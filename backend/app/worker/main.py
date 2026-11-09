"""Worker loop for M2-01/M2-04/M2-05.

Each poll runs the shard-dispatch outbox (repository_dispatch with retry and
reconciliation, M2-05) and the runner offline sweep (M2-04) so stale fixed
runners flip to offline and queued shards reach GitHub even when nobody
queries the API.
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
from ..services import dispatcher
from ..services.operations import record_worker_heartbeat
from ..services.runners import sweep_stale_runners

_STOP = False


def sweep_once(session: Session) -> int:
    """One runner offline sweep with the configured heartbeat timeout."""

    settings = get_settings()
    return sweep_stale_runners(session, timeout_seconds=settings.runner_heartbeat_timeout_seconds)


def dispatch_once(session: Session, transport=None) -> dispatcher.DispatchOutcome:
    """One outbox → repository_dispatch pass with configured retry policy."""

    return dispatcher.dispatch_pending_outbox(session, transport=transport)


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
                outcome = dispatch_once(session)
            if outcome.dispatched or outcome.retried or outcome.failed or outcome.recovered:
                print(
                    "worker: dispatch "
                    f"sent={outcome.dispatched} retried={outcome.retried} "
                    f"failed={outcome.failed} recovered={outcome.recovered}",
                    flush=True,
                )
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: dispatch failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                swept = sweep_once(session)
            if swept:
                print(f"worker: swept {swept} stale runner(s) offline", flush=True)
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: runner sweep failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                record_worker_heartbeat(session, pending_outbox=pending)
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: operations heartbeat failed: {exc}", flush=True)
        heartbeat.write_text(str(time.time()), encoding="utf-8")
        print(f"worker: heartbeat pending_outbox={pending}", flush=True)
        time.sleep(settings.worker_poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

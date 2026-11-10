"""Worker loop for M2-01/M2-04/M2-05/M4-02/M4-03.

Each poll runs the shard-dispatch outbox (repository_dispatch with retry and
reconciliation, M2-05), the execution-lifecycle reconciler (five timeouts,
cancellation finalization and lost-callback backfill, M4-02), the spot
capacity controller (M4-03) and the runner offline sweep (M2-04) so stale
fixed runners flip to offline, timed-out or cancelled attempts reach a
diagnosable terminal state and queued shards reach GitHub even when nobody
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
from ..services import capacity_control, dispatcher, lifecycle, reaper, reconciler
from ..services.operations import record_worker_heartbeat
from ..services.provisioning import register_builtin_provisioners
from ..services.runners import sweep_stale_runners

_STOP = False


def sweep_once(session: Session) -> int:
    """One runner offline sweep with the configured heartbeat timeout."""

    settings = get_settings()
    return sweep_stale_runners(session, timeout_seconds=settings.runner_heartbeat_timeout_seconds)


def dispatch_once(session: Session, transport=None) -> dispatcher.DispatchOutcome:
    """One outbox → repository_dispatch pass with configured retry policy."""

    return dispatcher.dispatch_pending_outbox(session, transport=transport)


def lifecycle_once(session: Session, *, now=None) -> dict[str, int]:
    """One execution-lifecycle sweep (five timeout classes, M4-02 A1)."""

    return reaper.sweep_lifecycle(session, settings=get_settings(), now=now)


def reconcile_once(session: Session, transport=None, *, now=None) -> dict[str, int]:
    """One reconcile pass: timeouts + cancellation + completion backfill (A2)."""

    return reconciler.reconcile(
        session, settings=get_settings(), now=now, transport=transport
    )


def capacity_once(session: Session, *, now=None) -> dict[str, int]:
    """One spot capacity pass over active pools (M4-03 B).

    Never raises for a provider failure: the controller records the reason and
    the next tick retries. The worker logs only a non-empty pass.
    """

    counts = capacity_control.reconcile_pools(
        session, settings=get_settings(), now=now
    )
    if any(counts.values()):
        print(
            "worker: capacity "
            f"scanned={counts['scanned']} "
            f"scaled_up={counts['scaled_up']} "
            f"scaled_down={counts['scaled_down']} "
            f"waiting_price={counts['waiting_price']} "
            f"errors={counts['errors']}",
            flush=True,
        )
    return counts


def _handle_signal(_signum: int, _frame: FrameType | None) -> None:
    global _STOP
    _STOP = True


def main() -> int:
    settings = get_settings()
    # M4-04: the worker is the only process that creates/releases spot
    # instances. Registration is lazy (no network) and idempotent.
    register_builtin_provisioners(settings)
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
            if (
                outcome.dispatched
                or outcome.retried
                or outcome.failed
                or outcome.recovered
                or outcome.queued
            ):
                print(
                    "worker: dispatch "
                    f"sent={outcome.dispatched} retried={outcome.retried} "
                    f"failed={outcome.failed} recovered={outcome.recovered} "
                    f"queued={outcome.queued}",
                    flush=True,
                )
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: dispatch failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                reconciled = reconcile_once(session)
            if any(reconciled.values()):
                # The reconciler merges the lifecycle sweep; keep the A1 log
                # line and add the cancellation/backfill counters.
                print(
                    "worker: lifecycle "
                    f"queue_timeout={reconciled.get(lifecycle.QUEUE_TIMEOUT, 0)} "
                    f"execution_timeout={reconciled.get(lifecycle.EXECUTION_TIMEOUT, 0)} "
                    f"scoring_timeout={reconciled.get(lifecycle.SCORING_TIMEOUT, 0)} "
                    f"upload_timeout={reconciled.get(lifecycle.UPLOAD_TIMEOUT, 0)} "
                    f"heartbeats={reconciled.get(lifecycle.HEARTBEAT_TIMEOUT, 0)}",
                    flush=True,
                )
                print(
                    "worker: reconcile "
                    f"cancelled={reconciled.get('cancelled', 0)} "
                    f"reconciled={reconciled.get('reconciled', 0)} "
                    f"budget_paused={reconciled.get('budget_paused', 0)} "
                    f"budget_settled={reconciled.get('budget_settled', 0)} "
                    f"heartbeat_timeout={reconciled.get(lifecycle.HEARTBEAT_TIMEOUT, 0)}",
                    flush=True,
                )
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: reconcile failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                capacity_once(session)
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: capacity control failed: {exc}", flush=True)
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

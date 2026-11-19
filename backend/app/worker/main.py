"""Worker loop for M2-01/M2-04/M2-05/M4-02/M4-03/M9.

Each poll runs the shard-dispatch outbox (repository_dispatch with retry and
reconciliation, M2-05), the execution-lifecycle reconciler (five timeouts,
cancellation finalization and lost-callback backfill, M4-02), the spot
capacity controller (M4-03), the runner offline sweep (M2-04) and the
self-evolution bad-case reporting pass (M9 #263/#288) so stale fixed runners
flip to offline, timed-out or cancelled attempts reach a diagnosable terminal
state, queued shards reach GitHub even when nobody queries the API, and both
settled-case and terminal-run bad cases reach the configured harness-evolution
workspace.
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
from ..services import (
    capacity_control,
    dispatcher,
    lifecycle,
    reaper,
    reconciler,
    self_evolution,
    trace_ingestion,
)
from ..services.dataset_imports import reap_stale_imports
from ..services.operations import record_worker_heartbeat
from ..services.provisioning import register_builtin_provisioners
from ..services.runners import cleanup_offline_runners, sweep_stale_runners

_STOP = False
#: M8-48 (#313): the retention cleanup is cheap but does not need to run on
#: every 5s poll; remember the last pass so a long-running worker only touches
#: the runner table once per configured interval.
_last_runner_cleanup_at: float | None = None


def sweep_once(session: Session) -> int:
    """One runner offline sweep with the configured heartbeat timeout."""

    settings = get_settings()
    return sweep_stale_runners(session, timeout_seconds=settings.runner_heartbeat_timeout_seconds)


def runner_cleanup_once(session: Session, *, now=None, force: bool = False) -> int:
    """One offline-runner retention pass, rate-limited by the worker interval."""

    global _last_runner_cleanup_at
    settings = get_settings()
    monotonic = time.monotonic()
    if (
        not force
        and _last_runner_cleanup_at is not None
        and monotonic - _last_runner_cleanup_at
        < max(0, settings.eval_runner_cleanup_interval_seconds)
    ):
        return 0
    _last_runner_cleanup_at = monotonic
    return cleanup_offline_runners(session, settings=settings, now=now)


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


def self_evolution_once(session: Session, *, now=None) -> dict[str, int]:
    """One self-evolution bad-case reporting pass (M9 #263)."""

    return self_evolution.process_pending_self_evolution_reports(session, now=now)


def self_evolution_enqueue_once(session: Session, *, now=None) -> int:
    """One settled-case early enqueue pass (M9 #288)."""

    settings = get_settings()
    return self_evolution.enqueue_settled_case_reports(
        session,
        limit=settings.self_evolution_batch_size,
        now=now,
    )


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
                backfilled = trace_ingestion.backfill_missing(
                    session, limit=settings.trace_ingestion_batch_size
                )
            if any(backfilled.values()):
                print(
                    "worker: trace backfill "
                    f"attempts={backfilled['attempts']} files={backfilled['files']}",
                    flush=True,
                )
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: trace backfill failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                ingestion = trace_ingestion.ingest_pending(
                    session, limit=settings.trace_ingestion_batch_size
                )
            if any(ingestion.values()):
                print(
                    "worker: trace ingestion "
                    f"attempts={ingestion['attempts']} files={ingestion['files']} "
                    f"ready={ingestion['ready']} degraded={ingestion['degraded']} "
                    f"failed={ingestion['failed']} skipped={ingestion['skipped']}",
                    flush=True,
                )
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: trace ingestion failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                published = trace_ingestion.publish_stale_cases(
                    session, limit=settings.trace_ingestion_batch_size
                )
            if any(published.values()):
                print(
                    "worker: trace publish "
                    f"scanned={published['scanned']} "
                    f"published={published['published']} "
                    f"events={published['events']}",
                    flush=True,
                )
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: trace publish failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                enqueued = self_evolution_enqueue_once(session)
            if enqueued:
                print(f"worker: self evolution enqueued={enqueued}", flush=True)
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: self evolution enqueue failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                capacity_once(session)
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: capacity control failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                reported = self_evolution_once(session)
            if reported["succeeded"] or reported["failed"] or reported["retried"]:
                print(
                    "worker: self evolution "
                    f"succeeded={reported['succeeded']} failed={reported['failed']} "
                    f"retried={reported['retried']}",
                    flush=True,
                )
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: self evolution reporting failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                swept = sweep_once(session)
            if swept:
                print(f"worker: swept {swept} stale runner(s) offline", flush=True)
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: runner sweep failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                cleaned = runner_cleanup_once(session)
            if cleaned:
                print(f"worker: cleaned {cleaned} expired offline runner(s)", flush=True)
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: runner cleanup failed: {exc}", flush=True)
        try:
            with SessionLocal() as session:
                reaped = reap_stale_imports(session)
            if reaped:
                print(f"worker: reaped {reaped} stale dataset import(s)", flush=True)
        except Exception as exc:  # pragma: no cover - retried on the next loop
            print(f"worker: dataset import sweep failed: {exc}", flush=True)
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

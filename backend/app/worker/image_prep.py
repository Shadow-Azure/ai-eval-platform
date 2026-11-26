"""M8 (#290): dedicated dataset image preparation worker.

Runs in its own compose service behind the ``image-prep`` profile and talks to
a dedicated Docker-in-Docker daemon (``DOCKER_HOST``), never the host socket.
It runs 1-4 independent preparation tasks (``DATASET_IMAGE_PREP_CONCURRENCY``),
each with its own DB session; the claim uses SKIP LOCKED so two tasks never pull
the same image. A sweep only runs when no task is active, so it cannot delete a
temporary tarball a concurrent save is writing. Startup and periodic sweeps
clear residue left by an interrupted process. Exceptions are caught per task so
one bad image never stops the loop.

A daemon thread refreshes the healthcheck file every ``HEARTBEAT_INTERVAL``
seconds. A single pull can take minutes, so writing the heartbeat only between
passes would let the container healthcheck go stale mid-pull.
"""

from __future__ import annotations

import json
import signal
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from types import FrameType

from ..config import get_settings
from ..db import SessionLocal
from ..services.dataset_image_prep import (
    DockerCliImagePreparer,
    PrepareOutcome,
    prepare_one,
    sweep,
)
from ..services.image_prep_health import record_image_prep_heartbeat

SWEEP_INTERVAL_SECONDS = 600.0
HEARTBEAT_INTERVAL_SECONDS = 10.0
DB_HEARTBEAT_INTERVAL_SECONDS = 30.0

_STOP = False


def _handle_signal(_signum: int, _frame: FrameType | None) -> None:
    global _STOP
    _STOP = True


def write_heartbeat(path: Path) -> None:
    """Best-effort healthcheck timestamp; a failure is logged, never fatal."""

    try:
        path.write_text(str(time.time()), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - healthcheck reports staleness
        print(f"image-prep: heartbeat write failed: {exc}", flush=True)


def heartbeat_loop(
    path: Path,
    stop: threading.Event,
    *,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Write ``path`` every ``interval`` seconds until ``stop`` is set."""

    while not stop.wait(interval):
        write_heartbeat(path)


def start_heartbeat(
    path: Path,
    *,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> tuple[threading.Thread, threading.Event]:
    """Start the daemon heartbeat thread and return it with its stop event."""

    stop = threading.Event()
    thread = threading.Thread(
        target=heartbeat_loop,
        args=(path, stop),
        kwargs={"interval": interval},
        name="image-prep-heartbeat",
        daemon=True,
    )
    thread.start()
    return thread, stop


def write_db_heartbeat() -> None:
    """Best-effort DB liveness row; the API/launch gate reads this instead of
    the container-local healthcheck file. A failure is logged, never fatal."""

    try:
        with SessionLocal() as session:
            record_image_prep_heartbeat(session)
    except Exception as exc:  # pragma: no cover - retried every interval
        print(f"image-prep: db heartbeat failed: {exc}", flush=True)


def db_heartbeat_loop(
    stop: threading.Event,
    *,
    interval: float = DB_HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    while not stop.wait(interval):
        write_db_heartbeat()


def start_db_heartbeat(
    *,
    interval: float = DB_HEARTBEAT_INTERVAL_SECONDS,
) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    thread = threading.Thread(
        target=db_heartbeat_loop,
        args=(stop,),
        kwargs={"interval": interval},
        name="image-prep-db-heartbeat",
        daemon=True,
    )
    thread.start()
    return thread, stop


def run_sweep(settings, docker, *, startup: bool = False) -> None:
    """Best-effort residue cleanup; a failure is logged, never fatal.

    ``startup=True`` is used for the first pass, which reclaims interrupted
    ``preparing`` rows and every temporary tarball; periodic passes are
    conservative and keep active leases and fresh tarballs.
    """

    try:
        with SessionLocal() as session:
            result = sweep(session, settings, docker=docker, startup=startup)
        if result.cleaned or result.cleanup_failed or result.tarballs_removed:
            print(
                "image-prep: sweep "
                f"startup={startup} "
                f"cleaned={result.cleaned} "
                f"cleanup_failed={result.cleanup_failed} "
                f"tarballs_removed={result.tarballs_removed}",
                flush=True,
            )
    except Exception as exc:  # pragma: no cover - retried on the next sweep
        print(f"image-prep: sweep failed: {exc}", flush=True)


def preparation_concurrency(settings) -> int:
    """Bounded worker pool size; 1 keeps the historical serial behaviour."""

    raw = getattr(settings, "dataset_image_prep_concurrency", 1) or 1
    return max(1, min(4, int(raw)))


def _prepare_task(settings, docker) -> PrepareOutcome:
    """One preparation attempt with its own DB session (pool worker)."""

    with SessionLocal() as session:
        return prepare_one(session, settings, docker=docker)


def log_prepare_outcome(outcome: PrepareOutcome) -> None:
    """Emit one searchable line per image plus its source-probe evidence."""

    if not outcome.claimed:
        return
    print(
        "image-prep: prepare "
        f"image={outcome.image} status={outcome.status} "
        f"source={outcome.source} size={outcome.size_bytes} "
        f"endpoint={outcome.endpoint} "
        f"pull_s={_seconds(outcome.pull_seconds)} "
        f"pull_bytes={outcome.pull_bytes} "
        f"save_s={_seconds(outcome.save_seconds)} "
        f"upload_s={_seconds(outcome.upload_seconds)} "
        f"tar_bytes={outcome.tar_bytes} "
        f"source_order={outcome.source_order or []} "
        f"source_skipped={outcome.source_skipped or []} "
        f"cleanup={outcome.local_cleanup} "
        f"error={(outcome.error or '')[:200]}",
        flush=True,
    )
    if outcome.source_probes:
        print(
            "image-prep: source-probe "
            + json.dumps(outcome.source_probes, ensure_ascii=False),
            flush=True,
        )
    if outcome.source_stats:
        print(
            "image-prep: source-stats "
            + json.dumps(outcome.source_stats, ensure_ascii=False),
            flush=True,
        )


def _seconds(value: float | None) -> str:
    return "?" if value is None else f"{value:.1f}"


def _handle_finished(future: Future) -> bool:
    """Log one finished task; return whether it claimed an image."""

    try:
        outcome = future.result()
    except Exception as exc:  # one bad image must not stop the loop
        print(f"image-prep: prepare failed: {exc}", flush=True)
        return False
    log_prepare_outcome(outcome)
    return bool(outcome.claimed)


def main() -> int:
    settings = get_settings()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    heartbeat = Path(settings.dataset_image_prep_heartbeat_path)
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    poll_seconds = max(0.1, float(settings.dataset_image_prep_poll_seconds))

    if not settings.dataset_image_prep_enabled:
        # Compose uses ``restart: unless-stopped`` and a healthcheck on this
        # file. Exiting 0 here would restart the container in a loop and never
        # turn the healthcheck green, so a disabled worker idles and keeps
        # writing the heartbeat until SIGTERM stops it.
        print("image-prep: disabled (DATASET_IMAGE_PREP_ENABLED=false)", flush=True)
        write_heartbeat(heartbeat)
        while not _STOP:
            time.sleep(poll_seconds)
            write_heartbeat(heartbeat)
        return 0

    docker = DockerCliImagePreparer(settings)

    heartbeat_thread, heartbeat_stop = start_heartbeat(heartbeat)
    db_heartbeat_thread, db_heartbeat_stop = start_db_heartbeat()
    # Make the worker visible to the launch gate immediately, before the first
    # (potentially multi-minute) pull starts.
    write_db_heartbeat()
    concurrency = preparation_concurrency(settings)
    print(f"image-prep: starting concurrency={concurrency}", flush=True)
    try:
        # Clear whatever a previous process left before claiming new work. The
        # startup sweep also reclaims preparing rows whose worker was killed.
        run_sweep(settings, docker, startup=True)
        last_sweep = time.monotonic()

        with ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="image-prep"
        ) as pool:
            pending: set[Future] = set()
            idle = False
            while not _STOP:
                # Once a periodic sweep is due, stop submitting new tasks and
                # let the active ones finish first. A sweep with no active task
                # cannot delete a tarball another task is still writing, and a
                # continuously busy queue still gets swept between batches.
                sweep_due = time.monotonic() - last_sweep >= SWEEP_INTERVAL_SECONDS
                if not sweep_due and not idle:
                    while len(pending) < concurrency and not _STOP:
                        pending.add(pool.submit(_prepare_task, settings, docker))
                if not pending:
                    if sweep_due:
                        run_sweep(settings, docker)
                        last_sweep = time.monotonic()
                        continue
                    # The queue was empty (or the sweep just ran): poll again
                    # after the normal interval instead of spinning.
                    time.sleep(poll_seconds)
                    idle = False
                    continue
                done, pending = wait(
                    pending, timeout=poll_seconds, return_when=FIRST_COMPLETED
                )
                claimed_any = False
                for future in done:
                    claimed_any = _handle_finished(future) or claimed_any
                if not done:
                    # A long pull is still active; keep waiting without
                    # submitting replacement tasks that only find an empty queue.
                    continue
                if claimed_any:
                    idle = False
                elif pending:
                    # Some work is still running but this batch found nothing
                    # new; stop refilling empty slots until a task finishes.
                    idle = True
                else:
                    idle = False
                if not pending and sweep_due:
                    run_sweep(settings, docker)
                    last_sweep = time.monotonic()
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=max(1.0, poll_seconds + 1.0))
        write_heartbeat(heartbeat)
        db_heartbeat_stop.set()
        db_heartbeat_thread.join(timeout=5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

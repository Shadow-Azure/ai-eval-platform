"""M8 (#290): dedicated dataset image preparation worker.

Runs in its own compose service behind the ``image-prep`` profile and talks to
a dedicated Docker-in-Docker daemon (``DOCKER_HOST``), never the host socket.
Each pass claims at most one due artifact, so an image pull cannot race another
pull and fill the data disk. Startup and periodic sweeps clear residue left by
an interrupted process. Exceptions are caught per pass so one bad image never
stops the loop.

A daemon thread refreshes the healthcheck file every ``HEARTBEAT_INTERVAL``
seconds. A single pull can take minutes, so writing the heartbeat only between
passes would let the container healthcheck go stale mid-pull.
"""

from __future__ import annotations

import signal
import threading
import time
from pathlib import Path
from types import FrameType

from ..config import get_settings
from ..db import SessionLocal
from ..services.dataset_image_prep import (
    DockerCliImagePreparer,
    prepare_one,
    sweep,
)

SWEEP_INTERVAL_SECONDS = 600.0
HEARTBEAT_INTERVAL_SECONDS = 10.0

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


def main() -> int:
    settings = get_settings()
    if not settings.dataset_image_prep_enabled:
        print("image-prep: disabled (DATASET_IMAGE_PREP_ENABLED=false)", flush=True)
        return 0
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    heartbeat = Path(settings.dataset_image_prep_heartbeat_path)
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    docker = DockerCliImagePreparer(settings)
    poll_seconds = max(0.1, float(settings.dataset_image_prep_poll_seconds))

    heartbeat_thread, heartbeat_stop = start_heartbeat(heartbeat)
    try:
        # Clear whatever a previous process left before claiming new work. The
        # startup sweep also reclaims preparing rows whose worker was killed.
        run_sweep(settings, docker, startup=True)
        last_sweep = time.monotonic()

        while not _STOP:
            try:
                with SessionLocal() as session:
                    outcome = prepare_one(session, settings, docker=docker)
                if outcome.claimed:
                    print(
                        "image-prep: prepare "
                        f"image={outcome.image} status={outcome.status} "
                        f"source={outcome.source} size={outcome.size_bytes} "
                        f"cleanup={outcome.local_cleanup} "
                        f"error={(outcome.error or '')[:200]}",
                        flush=True,
                    )
            except Exception as exc:  # one bad image must not stop the loop
                print(f"image-prep: prepare failed: {exc}", flush=True)

            if time.monotonic() - last_sweep >= SWEEP_INTERVAL_SECONDS:
                run_sweep(settings, docker)
                last_sweep = time.monotonic()

            time.sleep(poll_seconds)
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=max(1.0, poll_seconds + 1.0))
        write_heartbeat(heartbeat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""M8-45 (#307) live smoke: shard progress lease for the platform queue timeout.

Run manually (not collected by pytest):

    .venv/bin/python tests/integration/m8_45_queue_lease_smoke.py --out /tmp/m8-45-evidence.json

It builds a disposable SQLite control plane, dispatches real shard outbox rows
through the fake GitHub transport, drives the real started/failed callback path
and sweeps the real reaper with injected clocks. The scenario is the issue's
acceptance timeline: two 60-minute waves, a third case that finally goes idle,
a completely stuck shard and a pre-upgrade row without a lease.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def _aware(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url: str, *, method: str, body, headers, timeout: float):
        self.calls.append({"url": url, "method": method})
        return 204, ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/m8-45-evidence.json")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="m8-45-smoke-"))
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{tmp}/smoke.db",
            "REDIS_URL": "",
            "AUTH_SESSION_SECRET": "m8-45-smoke-session-secret",
            "AUTH_BOOTSTRAP_ADMIN_EMAIL": "admin@m8-45-smoke.local",
            "AUTH_BOOTSTRAP_ADMIN_PASSWORD": "m8-45-smoke-password",
            "EVAL_QUEUE_TIMEOUT_SECONDS": "3600",
            "EVAL_QUEUE_TIMEOUT_GRACE_SECONDS": "600",
            "EVAL_QUEUE_TIMEOUT_MAX_SECONDS": "82800",
            "EVAL_LIFECYCLE_BATCH_SIZE": "100",
            "GITHUB_TOKEN": "m8-45-smoke-token",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "app.seed"], cwd=BACKEND, env=env, check=True, capture_output=True
    )

    inherited = ("RUNNER_", "DATABASE_URL", "S3_", "AUTH_", "EVAL_", "REDIS_", "GITHUB_")
    os.environ.update({key: env[key] for key in env if key.startswith(inherited)})
    sys.path.insert(0, str(BACKEND))

    from app.config import get_settings  # noqa: PLC0415
    from app.db import SessionLocal  # noqa: PLC0415
    from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox  # noqa: PLC0415
    from app.seed import ensure_default_scope  # noqa: PLC0415
    from app.services import case_attempts, dispatcher, evaluations, lifecycle, reaper  # noqa: PLC0415

    settings = get_settings()
    transport = FakeTransport()
    timeline = datetime.now(UTC)

    def create_run(key: str, case_keys: list[str], *, case_timeout: int = 3600) -> EvalRun:
        with SessionLocal() as session:
            ensure_default_scope(session)
            run, _ = evaluations.create_synthetic_evaluation(
                session,
                name=f"M8-45 smoke {key}",
                case_keys=case_keys,
                shard_size=64,
                requested_concurrency=8,
                case_timeout_seconds=case_timeout,
                idempotency_key=key,
                created_by="m8-45-smoke",
                settings=settings,
                now=timeline,
            )
            # Outbox rows default available_at to the real clock at flush time;
            # align them with the injected timeline so dispatch is deterministic.
            session.query(Outbox).filter(Outbox.aggregate_id == run.id).update(
                {"available_at": timeline}
            )
            session.commit()
            return run

    def dispatch(run_id: str, now: datetime) -> int:
        with SessionLocal() as session:
            outcome = dispatcher.dispatch_pending_outbox(
                session, settings=settings, transport=transport, now=now
            )
            return outcome.dispatched

    def attempts(run_id: str) -> list[EvalCaseAttempt]:
        with SessionLocal() as session:
            return list(
                session.query(EvalCaseAttempt)
                .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
                .filter(EvalCaseRun.eval_run_id == run_id)
                .order_by(EvalCaseAttempt.eval_case_run_id)
                .all()
            )

    def start(case_run_id: str, now: datetime) -> None:
        with SessionLocal() as session:
            case_attempts.handle_started(
                session,
                payload={
                    "case_run_id": case_run_id,
                    "attempt": 1,
                    "event_id": f"start-{case_run_id}",
                    "runner_id": "m8-45-smoke-runner",
                },
                now=now,
            )

    def fail(case_run_id: str, now: datetime) -> None:
        with SessionLocal() as session:
            case_attempts.handle_failed(
                session,
                payload={
                    "case_run_id": case_run_id,
                    "attempt": 1,
                    "sequence": 2,
                    "event_id": f"fail-{case_run_id}",
                    "error_code": "runtime_error",
                    "error_summary": "smoke failure",
                    "retryable": False,
                },
                now=now,
            )

    def sweep(now: datetime) -> dict[str, int]:
        with SessionLocal() as session:
            # A live runner keeps heartbeating; the smoke is about the queue
            # lease, so keep running attempts inside the heartbeat window.
            session.query(EvalCaseAttempt).filter(
                EvalCaseAttempt.status == "running"
            ).update({"heartbeat_at": now})
            session.commit()
            return reaper.sweep_lifecycle(session, settings=settings, now=now)

    evidence: dict = {}
    try:
        run = create_run("m8-45-waves", ["wave-a", "wave-b", "wave-c"])
        assert dispatch(run.id, timeline) == 1
        rows = attempts(run.id)
        evidence["dispatch"] = {
            "queue_started_at": _aware(rows[0].queue_started_at).isoformat(),
            "queue_deadline": _aware(rows[0].queue_deadline).isoformat(),
            "lease_seconds": int(
                (_aware(rows[0].queue_deadline) - _aware(rows[0].queue_started_at)).total_seconds()
            ),
        }
        assert evidence["dispatch"]["lease_seconds"] == 4200

        with SessionLocal() as session:
            case_ids = {
                case.case_key: case.id
                for case in session.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id)
            }

        start(case_ids["wave-a"], timeline + timedelta(seconds=1))
        evidence["after_first_start_deadline"] = _aware(
            attempts(run.id)[0].queue_deadline
        ).isoformat()
        counts = sweep(timeline + timedelta(seconds=3600))
        evidence["one_hour_queue_timeouts"] = counts[lifecycle.QUEUE_TIMEOUT]
        assert counts[lifecycle.QUEUE_TIMEOUT] == 0

        fail(case_ids["wave-a"], timeline + timedelta(seconds=3600))
        start(case_ids["wave-b"], timeline + timedelta(seconds=3601))
        counts = sweep(timeline + timedelta(seconds=7200))
        evidence["two_hour_queue_timeouts"] = counts[lifecycle.QUEUE_TIMEOUT]
        assert counts[lifecycle.QUEUE_TIMEOUT] == 0

        fail(case_ids["wave-b"], timeline + timedelta(seconds=7200))
        counts = sweep(timeline + timedelta(seconds=11401))
        evidence["idle_after_last_wave"] = counts[lifecycle.QUEUE_TIMEOUT]
        assert counts[lifecycle.QUEUE_TIMEOUT] == 1
        with SessionLocal() as session:
            timeout_event = (
                session.query(EvalEvent)
                .filter(
                    EvalEvent.eval_run_id == run.id,
                    EvalEvent.event_type == "case.timeout",
                )
                .one()
            )
            payload = timeout_event.payload_json
        evidence["queue_timeout_payload"] = {
            key: payload.get(key)
            for key in (
                "timeout_class",
                "queue_wait_seconds",
                "last_progress_at",
                "queue_deadline",
                "shard_size",
                "concurrency",
            )
        }
        assert payload["timeout_class"] == lifecycle.QUEUE_TIMEOUT
        assert payload["queue_wait_seconds"] == 11401
        assert payload["last_progress_at"] is not None

        stuck = create_run("m8-45-stuck", ["stuck-a", "stuck-b"])
        assert dispatch(stuck.id, timeline) == 1
        counts = sweep(timeline + timedelta(seconds=4201))
        evidence["stuck_shard_queue_timeouts"] = counts[lifecycle.QUEUE_TIMEOUT]
        assert counts[lifecycle.QUEUE_TIMEOUT] == 2

        legacy = create_run("m8-45-legacy", ["legacy-a"])
        assert dispatch(legacy.id, timeline) == 1
        with SessionLocal() as session:
            attempt = (
                session.query(EvalCaseAttempt)
                .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
                .filter(EvalCaseRun.eval_run_id == legacy.id)
                .one()
            )
            attempt.queue_started_at = None
            attempt.queue_deadline = None
            attempt.phase_changed_at = timeline - timedelta(seconds=3601)
            session.commit()
        counts = sweep(timeline)
        evidence["legacy_fallback_queue_timeouts"] = counts[lifecycle.QUEUE_TIMEOUT]
        assert counts[lifecycle.QUEUE_TIMEOUT] == 1

        evidence["ok"] = True
        Path(args.out).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0
    finally:
        print(f"smoke database: {tmp}/smoke.db", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())

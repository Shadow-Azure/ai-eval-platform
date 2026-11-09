#!/usr/bin/env python3
"""M4-01 live smoke: real uvicorn + real runner processes under a concurrency cap.

NOT collected by pytest (name does not start with ``test_``). Run manually:

    .venv/bin/python tests/integration/m4_01_concurrency_smoke.py \
        --out /tmp/m4-01-evidence.json

It starts a disposable SQLite control plane, registers two runners, creates a
four-case / one-shard evaluation with ``requested_concurrency=2``, then runs
four ``runner/scripts/real-task.mjs --case`` processes against it while
sampling the running-case count. The evaluation's outbox row is used as the
dispatch payload (no GitHub call is made), so this exercises the real manifest
/ token / callback path and the started-callback hard cap.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError(f"service did not become ready: {url}")


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/m4-01-evidence.json")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="m4-01-smoke-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{tmp}/smoke.db",
            "REDIS_URL": "",
            "AUTH_SESSION_SECRET": "m4-smoke-session-secret",
            "AUTH_BOOTSTRAP_ADMIN_EMAIL": "admin@m4-smoke.local",
            "AUTH_BOOTSTRAP_ADMIN_PASSWORD": "m4-smoke-password",
            "RUNNER_TASK_SECRET": "m4-smoke-task-secret",
            "RUNNER_CALLBACK_BASE_URL": base,
            "RUNNER_HEARTBEAT_TOKEN": "",
            "S3_ENDPOINT": "",
            "S3_ACCESS_KEY": "",
            "S3_SECRET_KEY": "",
            "GITHUB_TOKEN": "",
            "EVAL_MAX_CASES": "1000",
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

    os.environ.update({key: env[key] for key in env if key not in os.environ or key.startswith(("RUNNER_", "DATABASE_URL", "S3_", "AUTH_", "EVAL_", "REDIS_", "GITHUB_"))})
    sys.path.insert(0, str(BACKEND))
    from app.db import SessionLocal  # noqa: PLC0415
    from app.models import EvalCaseRun, Outbox, ResourcePool, Runner  # noqa: PLC0415
    from app.seed import ensure_default_scope  # noqa: PLC0415
    from app.services import evaluations  # noqa: PLC0415

    log = (tmp / "uvicorn.log").open("w")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    timeline: list[dict] = []
    try:
        wait_http(f"{base}/healthz")
        for index in (1, 2):
            post_json(
                f"{base}/runner/v1/runners/heartbeat",
                {
                    "runner_id": f"m4-smoke-runner-{index}",
                    "name": f"m4-smoke-runner-{index}",
                    "labels": ["self-hosted", "linux", "eval"],
                    "status": "online",
                    "current_case_attempt_id": None,
                },
            )

        with SessionLocal() as session:
            ensure_default_scope(session)
            # Heartbeats register into the configured fixed pool; the run must
            # be bound to that same pool or the capacity gate sees zero runners.
            pool = (
                session.query(ResourcePool)
                .join(Runner, Runner.resource_pool_id == ResourcePool.id)
                .filter(Runner.provider_runner_id == "m4-smoke-runner-1")
                .one()
            )
            run, _created = evaluations.create_synthetic_evaluation(
                session,
                name="M4-01 concurrency smoke",
                case_keys=[f"smoke-case-{index}" for index in range(4)],
                shard_size=4,
                requested_concurrency=2,
                resource_pool_id=pool.id,
                idempotency_key="m4-smoke",
                created_by="m4-smoke",
            )
            row = session.query(Outbox).filter(Outbox.aggregate_id == run.id).one()
            dispatch = row.payload_json["client_payload"]
            case_ids = [
                case.id
                for case in session.query(EvalCaseRun)
                .filter(EvalCaseRun.eval_run_id == run.id)
                .order_by(EvalCaseRun.ordinal)
            ]
            assert len(case_ids) == 4, case_ids

        dispatch_path = tmp / "dispatch.json"
        dispatch_path.write_text(json.dumps(dispatch, indent=2), encoding="utf-8")

        started_at = time.monotonic()
        procs = []
        for index, case_id in enumerate(case_ids):
            runner_env = env.copy()
            runner_env.update(
                {
                    "RUNNER_ID": f"m4-smoke-runner-{(index % 2) + 1}",
                    "RUNNER_STATE_DIR": str(tmp / f"state-{index}"),
                    "RUNNER_OBJECT_ROOT": str(tmp / f"objects-{index}"),
                    "RUNNER_CAPACITY_WAIT_SECONDS": "30",
                }
            )
            procs.append(
                (
                    index,
                    subprocess.Popen(
                        [
                            "node",
                            str(REPO / "runner/scripts/real-task.mjs"),
                            "--dispatch",
                            str(dispatch_path),
                            "--case",
                            case_id,
                            "--state-dir",
                            str(tmp / f"state-{index}"),
                            "--object-store-root",
                            str(tmp / f"objects-{index}"),
                        ],
                        cwd=REPO,
                        env=runner_env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    ),
                )
            )

        deadline = time.monotonic() + args.timeout
        peak = 0
        while any(proc.poll() is None for _index, proc in procs) and time.monotonic() < deadline:
            try:
                with SessionLocal() as session:
                    running = sum(
                        1
                        for case in session.query(EvalCaseRun)
                        .filter(EvalCaseRun.eval_run_id == run.id)
                        .all()
                        if case.execution_status == "running"
                    )
            except Exception:  # noqa: BLE001 - SQLite may be briefly locked
                running = None
            if running is not None:
                peak = max(peak, running)
                timeline.append({"t": round(time.monotonic() - deadline + args.timeout, 3), "running": running})
            time.sleep(0.05)

        results = []
        for index, proc in procs:
            stdout, stderr = proc.communicate(timeout=30)
            results.append(
                {
                    "case_index": index,
                    "exit_code": proc.returncode,
                    "status": (json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}).get("status"),
                    "stderr_tail": stderr.strip().splitlines()[-1:] if stderr.strip() else [],
                }
            )

        with SessionLocal() as session:
            run = session.get(type(run), run.id)
            assert run is not None
            cases = [
                {
                    "case_key": case.case_key,
                    "execution_status": case.execution_status,
                    "result_status": case.result_status,
                }
                for case in session.query(EvalCaseRun)
                .filter(EvalCaseRun.eval_run_id == run.id)
                .order_by(EvalCaseRun.ordinal)
            ]
            attempt_count = int(
                session.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id).count()
            )
            packed = {
                "eval_run_id": run.id,
                "requested_concurrency": run.requested_concurrency,
                "effective_concurrency": run.effective_concurrency,
                "queue_reason": run.queue_reason,
                "dispatch_key": dispatch["dispatch_key"],
                "case_count": len(cases),
                "attempt_count": attempt_count,
                "peak_running": peak,
                "timeline": timeline,
                "runner_results": results,
                "cases": cases,
                "elapsed_seconds": round(time.monotonic() - started_at, 2),
            }

        Path(args.out).write_text(json.dumps(packed, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: packed[key] for key in ("peak_running", "effective_concurrency", "cases", "runner_results")}, ensure_ascii=False, indent=2))

        ok = (
            all(item["exit_code"] == 0 for item in results)
            and all(item["execution_status"] == "succeeded" for item in cases)
            and peak <= 2
            and packed["effective_concurrency"] == 2
        )
        return 0 if ok else 1
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())

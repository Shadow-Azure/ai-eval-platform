#!/usr/bin/env python3
"""M5-08 live smoke: custom-label spot pool shards execute with concurrency 5.

NOT collected by pytest (name does not start with ``test_``). Run manually:

    python tests/integration/m5_08_runner_callback_smoke.py --out /tmp/m5-08-evidence.json

It starts a disposable SQLite control plane, registers five online runners in a
spot pool whose ``labels_json.runs_on`` is a custom label set, creates a
five-case / five-shard evaluation with ``requested_concurrency=5`` and runs
five ``runner/scripts/real-task.mjs --case`` processes against it. The smoke
asserts that every dispatch and manifest froze the custom labels, that the
callback base URL is the configured VPC-style address, that peak running
reaches 5, and that 5/5 cases report results.
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
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
CUSTOM_LABELS = ["self-hosted", "linux", "eval", "m5-08-spot"]
CASE_COUNT = 5


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/m5-08-evidence.json")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="m5-08-smoke-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{tmp}/smoke.db",
            "REDIS_URL": "",
            "AUTH_SESSION_SECRET": "m5-08-smoke-session-secret",
            "AUTH_BOOTSTRAP_ADMIN_EMAIL": "admin@m5-08-smoke.local",
            "AUTH_BOOTSTRAP_ADMIN_PASSWORD": "m5-08-smoke-password",
            "RUNNER_TASK_SECRET": "m5-08-smoke-task-secret",
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

    inherited = ("RUNNER_", "DATABASE_URL", "S3_", "AUTH_", "EVAL_", "REDIS_", "GITHUB_")
    os.environ.update(
        {key: env[key] for key in env if key not in os.environ or key.startswith(inherited)}
    )
    sys.path.insert(0, str(BACKEND))
    from app.db import SessionLocal  # noqa: PLC0415
    from app.defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID  # noqa: PLC0415
    from app.models import (  # noqa: PLC0415
        EvalCaseAttempt,
        EvalCaseRun,
        Outbox,
        ResourcePool,
        Runner,
    )
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

    started_at = time.monotonic()
    try:
        wait_http(f"{base}/healthz")
        now = datetime.now(UTC)
        with SessionLocal() as session:
            ensure_default_scope(session)
            pool = ResourcePool(
                id="pool-m5-08-smoke",
                tenant_id=str(DEFAULT_TENANT_ID),
                project_id=str(DEFAULT_PROJECT_ID),
                name="m5-08 custom labels",
                provider_key="aliyun",
                provisioning_mode="spot",
                min_capacity=0,
                max_capacity=CASE_COUNT,
                labels_json={"runs_on": CUSTOM_LABELS},
            )
            session.add(pool)
            for index in range(CASE_COUNT):
                session.add(
                    Runner(
                        id=f"runner-m5-08-{index}",
                        tenant_id=str(DEFAULT_TENANT_ID),
                        resource_pool_id=pool.id,
                        provider_runner_id=f"m5-08-spot-{index}",
                        name=f"m5-08-spot-{index}",
                        labels_json={"runs_on": CUSTOM_LABELS},
                        status="online",
                        registered_at=now,
                        last_heartbeat_at=now,
                    )
                )
            session.flush()
            run, _created = evaluations.create_synthetic_evaluation(
                session,
                name="M5-08 runner callback smoke",
                case_keys=[f"m5-08-case-{index}" for index in range(CASE_COUNT)],
                shard_size=1,
                requested_concurrency=CASE_COUNT,
                resource_pool_id=pool.id,
                idempotency_key="m5-08-smoke",
                created_by="m5-08-smoke",
            )
            rows = list(
                session.query(Outbox)
                .filter(Outbox.aggregate_id == run.id)
                .order_by(Outbox.dispatch_key)
                .all()
            )
            assert len(rows) == CASE_COUNT, len(rows)
            case_ids = [
                case.id
                for case in session.query(EvalCaseRun)
                .filter(EvalCaseRun.eval_run_id == run.id)
                .order_by(EvalCaseRun.ordinal)
            ]
            assert len(case_ids) == CASE_COUNT, case_ids
            dispatches = []
            for row in rows:
                dispatch = row.payload_json["client_payload"]
                manifest = json.loads(row.payload_json["manifest_text"])
                assert dispatch["runs_on"] == CUSTOM_LABELS, dispatch["runs_on"]
                assert manifest["runs_on"] == CUSTOM_LABELS, manifest["runs_on"]
                assert manifest["callback_base_url"] == base
                dispatches.append(dispatch)

        dispatch_paths = []
        for index, dispatch in enumerate(dispatches):
            path = tmp / f"dispatch-{index}.json"
            path.write_text(json.dumps(dispatch, indent=2), encoding="utf-8")
            dispatch_paths.append(path)

        procs = []
        for index, case_id in enumerate(case_ids):
            runner_env = env.copy()
            runner_env.update(
                {
                    "RUNNER_ID": f"m5-08-spot-{index}",
                    "RUNNER_CAPACITY_WAIT_SECONDS": "60",
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
                            str(dispatch_paths[index]),
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
        timeline: list[dict] = []
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
                timeline.append(
                    {
                        "t": round(args.timeout - (deadline - time.monotonic()), 2),
                        "running": running,
                    }
                )
            time.sleep(0.05)

        results = []
        for index, proc in procs:
            stdout, stderr = proc.communicate(timeout=30)
            results.append(
                {
                    "case_index": index,
                    "exit_code": proc.returncode,
                    "status": (
                        json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
                    ).get("status"),
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
            attempts = (
                session.query(EvalCaseAttempt)
                .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
                .filter(EvalCaseRun.eval_run_id == run.id)
                .all()
            )
            events = []
            for attempt in attempts:
                if attempt.started_at and attempt.ended_at:
                    events.append((attempt.started_at, 1))
                    events.append((attempt.ended_at, -1))
            events.sort(key=lambda item: (item[0], item[1]))
            overlap = current = 0
            for _at, delta in events:
                current += delta
                overlap = max(overlap, current)
            packed = {
                "eval_run_id": run.id,
                "requested_concurrency": run.requested_concurrency,
                "effective_concurrency": run.effective_concurrency,
                "queue_reason": run.queue_reason,
                "case_count": len(cases),
                "attempt_count": len(attempts),
                "max_attempt_overlap": overlap,
                "dispatch_runs_on": dispatches[0]["runs_on"],
                "callback_base_url": dispatches[0]["manifest_url"].split("/runner/v1/")[0],
                "peak_running": peak,
                "timeline": timeline,
                "runner_results": results,
                "cases": cases,
                "elapsed_seconds": round(time.monotonic() - started_at, 2),
            }

        Path(args.out).write_text(json.dumps(packed, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(packed, ensure_ascii=False, indent=2))

        ok = (
            all(item["exit_code"] == 0 for item in results)
            and all(item["execution_status"] == "succeeded" for item in cases)
            and peak <= CASE_COUNT
            and packed["effective_concurrency"] == CASE_COUNT
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

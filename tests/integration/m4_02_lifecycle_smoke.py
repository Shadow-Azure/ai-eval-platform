#!/usr/bin/env python3
"""M4-02 live smoke: cancellation, timeout reaping and reconciliation.

Run manually (not collected by pytest):

    .venv/bin/python tests/integration/m4_02_lifecycle_smoke.py --out /tmp/m4-02-evidence.json

It starts a disposable SQLite control plane (uvicorn), drives the real HTTP
started/cancel/completed paths, then exercises the reaper and reconciler in
process against the same database. No GitHub call is made: a fake transport
records the find/cancel requests.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/m4-02-evidence.json")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="m4-02-smoke-"))
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
            "GITHUB_TOKEN": "smoke-token",
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

    os.environ.update(
        {key: env[key] for key in env if key.startswith(("RUNNER_", "DATABASE_URL", "S3_", "AUTH_", "EVAL_", "REDIS_", "GITHUB_"))}
    )
    sys.path.insert(0, str(BACKEND))
    from app.db import SessionLocal  # noqa: PLC0415
    from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox, ResourcePool, Runner  # noqa: PLC0415
    from app.seed import ensure_default_scope  # noqa: PLC0415
    from app.services import evaluations, reaper, reconciler  # noqa: PLC0415

    log = (tmp / "uvicorn.log").open("w")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def post(path: str, payload: dict, headers: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{base}{path}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"content-type": "application/json", **(headers or {})},
        )
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, {"raw": body}

    evidence: dict = {"cancel": {}, "timeout": {}, "reconcile": {}}
    try:
        wait_http(f"{base}/healthz")
        login = post("/api/v1/auth/login", {"email": "admin@m4-smoke.local", "password": "m4-smoke-password"})
        assert login[0] == 200, login
        post(
            "/runner/v1/runners/heartbeat",
            {
                "runner_id": "m4-lifecycle-runner",
                "name": "m4-lifecycle-runner",
                "labels": ["self-hosted", "linux", "eval"],
                "status": "online",
                "current_case_attempt_id": None,
            },
        )

        with SessionLocal() as session:
            tenant, _workspace, project = ensure_default_scope(session)
            pool = ResourcePool(
                tenant_id=tenant.id,
                project_id=project.id,
                name="m4-lifecycle-pool",
                provider_key="fixed",
                labels_json={"kind": "fixed"},
                status="active",
            )
            session.add(pool)
            session.flush()
            session.add(
                Runner(
                    tenant_id=tenant.id,
                    resource_pool_id=pool.id,
                    provider_runner_id="m4-lifecycle-runner",
                    name="m4-lifecycle-runner",
                    labels_json=["self-hosted", "linux", "eval"],
                    status="online",
                    last_heartbeat_at=datetime.now(UTC),
                )
            )
            session.commit()
            run, _ = evaluations.create_synthetic_evaluation(
                session,
                name="M4-02 cancel smoke",
                case_keys=["cancel-a", "cancel-b"],
                shard_size=2,
                requested_concurrency=1,
                resource_pool_id=pool.id,
                idempotency_key="m4-02-cancel",
                created_by="m4-smoke",
            )
            cases = list(
                session.query(EvalCaseRun)
                .filter(EvalCaseRun.eval_run_id == run.id)
                .order_by(EvalCaseRun.ordinal)
            )
            case_a, case_b = cases[0].id, cases[1].id
            manifest_url = session.query(Outbox).filter(Outbox.aggregate_id == run.id).one().payload_json[
                "client_payload"
            ]["manifest_url"]

        with opener.open(urllib.request.Request(manifest_url), timeout=10) as manifest_response:
            token = manifest_response.headers.get("X-Runner-Task-Token")
        assert token, "manifest did not issue a task token"
        auth = {"authorization": f"Bearer {token}", "x-runner-id": "m4-lifecycle-runner"}

        started = post(
            f"/runner/v1/case-runs/{case_a}/started",
            {
                "schema_version": "1.0",
                "event_id": "evt-cancel-a-start",
                "case_run_id": case_a,
                "attempt": 1,
                "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            auth,
        )
        assert started[0] == 200, started

        cancel = post(f"/api/v1/evaluations/{run.id}/cancel", {})
        assert cancel[0] == 200, cancel
        with SessionLocal() as session:
            run_row = session.get(EvalRun, run.id)
            attempts = {
                attempt.eval_case_run_id: attempt.status
                for attempt in session.query(EvalCaseAttempt).all()
            }
            evidence["cancel"] = {
                "run_status": run_row.status,
                "attempt_a": attempts.get(case_a),
                "attempt_b": attempts.get(case_b),
            }

        rejected = post(
            f"/runner/v1/case-runs/{case_b}/started",
            {
                "schema_version": "1.0",
                "event_id": "evt-cancel-b-start",
                "case_run_id": case_b,
                "attempt": 1,
                "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            auth,
        )
        evidence["cancel"]["started_after_cancel_status"] = rejected[0]

        completed = post(
            f"/runner/v1/case-runs/{case_a}/completed",
            {
                "schema_version": "1.0",
                "event_id": "evt-cancel-a-completed",
                "case_run_id": case_a,
                "attempt": 1,
                "sequence": 2,
                "execution_status": "succeeded",
                "result_status": "resolved",
                "score_manifest": {
                    "object_key": f"evaluations/{run.id}/score.json",
                    "sha256": "a" * 64,
                    "size": 1,
                },
                "summary": {"duration_ms": 10, "input_tokens": 0, "output_tokens": 0},
            },
            auth,
        )
        evidence["cancel"]["completed_status"] = completed[0]
        with SessionLocal() as session:
            reconciler.reconcile(session, settings=__import__("app.config", fromlist=["get_settings"]).get_settings())
            evidence["cancel"]["run_after_reconcile"] = session.get(EvalRun, run.id).status

        # --- timeout class: an execution timeout is reaped deterministically ---
        with SessionLocal() as session:
            timeout_run, _ = evaluations.create_synthetic_evaluation(
                session,
                name="M4-02 timeout smoke",
                case_keys=["timeout-a"],
                shard_size=1,
                requested_concurrency=1,
                idempotency_key="m4-02-timeout",
                created_by="m4-smoke",
            )
            attempt = session.query(EvalCaseAttempt).filter(
                EvalCaseAttempt.eval_case_run_id.in_(
                    session.query(EvalCaseRun.id).filter(EvalCaseRun.eval_run_id == timeout_run.id)
                )
            ).one()
            attempt.status = "running"
            attempt.started_at = datetime.now(UTC) - timedelta(hours=3)
            attempt.heartbeat_at = datetime.now(UTC)
            session.commit()
            counts = reaper.sweep_lifecycle(
                session,
                settings=__import__("app.config", fromlist=["get_settings"]).get_settings(),
            )
            session.expire_all()
            evidence["timeout"] = {
                "counts": counts,
                "attempt_status": session.get(type(attempt), attempt.id).status,
                "error_code": session.get(type(attempt), attempt.id).error_code,
            }

        # --- reconciler: find + cancel the GitHub run, idempotently ---
        class FakeTransport:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def __call__(self, url: str, *, method: str, body, headers, timeout: float):
                self.calls.append({"url": url, "method": method})
                if method == "GET" and "/actions/runs?" in url:
                    return 200, json.dumps(
                        {"workflow_runs": [{"id": 4242, "display_title": f"eval-shard {reconcile_run.id}:shard_000:1"}]}
                    )
                if method == "POST" and url.endswith("/cancel"):
                    return 202, ""
                return 200, "{}"

        with SessionLocal() as session:
            reconcile_run, _ = evaluations.create_synthetic_evaluation(
                session,
                name="M4-02 reconcile smoke",
                case_keys=["reconcile-a"],
                shard_size=1,
                requested_concurrency=1,
                idempotency_key="m4-02-reconcile",
                created_by="m4-smoke",
            )
            attempt = session.query(EvalCaseAttempt).filter(
                EvalCaseAttempt.eval_case_run_id.in_(
                    session.query(EvalCaseRun.id).filter(EvalCaseRun.eval_run_id == reconcile_run.id)
                )
            ).one()
            attempt.status = "running"
            attempt.heartbeat_at = datetime.now(UTC)
            session.commit()

        post(f"/api/v1/evaluations/{reconcile_run.id}/cancel", {})
        transport = FakeTransport()
        settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
        with SessionLocal() as session:
            first = reconciler.reconcile(session, settings=settings, transport=transport)
        with SessionLocal() as session:
            second = reconciler.reconcile(session, settings=settings, transport=transport)
            events = [
                event.event_type
                for event in session.query(EvalEvent)
                .filter(EvalEvent.eval_run_id == reconcile_run.id)
                .all()
            ]
            evidence["reconcile"] = {
                "first": first,
                "second": second,
                "github_run_id": session.get(EvalRun, reconcile_run.id).github_run_id,
                "cancel_calls": [call for call in transport.calls if call["method"] == "POST"],
                "events": sorted(set(events)),
            }

        Path(args.out).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))

        ok = (
            evidence["cancel"]["attempt_b"] == "cancelled"
            and evidence["cancel"]["attempt_a"] == "running"
            and evidence["cancel"]["started_after_cancel_status"] in (409, 410)
            and evidence["cancel"]["run_after_reconcile"] == "cancelled"
            and evidence["timeout"]["attempt_status"] == "timeout"
            and evidence["timeout"]["error_code"] == "execution_timeout"
            and evidence["reconcile"]["github_run_id"] == "4242"
            and len(evidence["reconcile"]["cancel_calls"]) == 1
            and "eval.cancel_requested" in evidence["reconcile"]["events"]
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

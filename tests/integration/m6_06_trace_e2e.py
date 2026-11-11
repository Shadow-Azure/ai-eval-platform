#!/usr/bin/env python3
"""M6-06 real Claude Code trace E2E.

The workflow runs this on the fixed Linux runner. Claude Code 2.1.278 is the
real executable; only its provider endpoint is deterministic. The harness then
uses the production runner callbacks, S3/OSS presigned uploads, worker
ingestion, trace APIs, raw download and audit path.
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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
ADMIN_EMAIL = "admin@m6-e2e.local"
ADMIN_PASSWORD = "m6-e2e-admin-password"
VIEWER_EMAIL = "viewer@m6-e2e.local"
VIEWER_PASSWORD = "m6-e2e-viewer-password"
SENSITIVE_SENTINEL = "m6-e2e-provider-secret-never-persist"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_http(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError(f"service did not become ready: {url}")


class S3Stub:
    """Minimal S3-compatible object store used only for local GUI development."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.server: ThreadingHTTPServer | None = None

    def start(self) -> str:
        store = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _key(self) -> str:
                path = urllib.parse.urlparse(self.path).path.lstrip("/")
                parts = path.split("/", 1)
                return parts[1] if len(parts) == 2 else ""

            def do_PUT(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length", "0"))
                data = self.rfile.read(length)
                store.objects[self._key()] = (
                    data,
                    self.headers.get("content-type", "application/octet-stream"),
                )
                self.send_response(200)
                self.send_header("etag", f'"{len(data)}"')
                self.end_headers()

            def do_HEAD(self) -> None:  # noqa: N802
                item = store.objects.get(self._key())
                if item is None:
                    self.send_error(404)
                    return
                data, content_type = item
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.send_header("content-type", content_type)
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                if self.path.rstrip("/") == "/healthz":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                    return
                item = store.objects.get(self._key())
                if item is None:
                    self.send_error(404)
                    return
                data, content_type = item
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.send_header("content-type", content_type)
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | list[Any] | str]:
        status, payload, _headers = self.request_with_headers(method, path, body)
        return status, payload

    def request_with_headers(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | list[Any] | str, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"content-type": "application/json"} if data is not None else {},
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                content_type = response.headers.get("content-type", "")
                return (
                    response.status,
                    json.loads(raw)
                    if "json" in content_type
                    else raw.decode("utf-8", "replace"),
                    response.headers,
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw.decode("utf-8", "replace")
            return exc.code, payload, exc.headers

    def login(self, email: str, password: str) -> None:
        status, payload = self.request(
            "POST",
            "/api/v1/auth/login",
            {"email": email, "password": password},
        )
        if status != 200:
            raise AssertionError(f"login failed for {email}: {status} {payload}")


def _flatten_sessions(
    tree: list[dict[str, Any]], seen: set[str] | None = None
) -> list[dict[str, Any]]:
    seen = seen or set()
    result: list[dict[str, Any]] = []
    for node in tree:
        if node["id"] in seen:
            continue
        seen.add(node["id"])
        result.append(node)
        result.extend(_flatten_sessions(node.get("children") or [], seen))
    return result


def _read_dispatch(tmp: Path, row_payload: dict[str, Any], name: str) -> Path:
    path = tmp / name
    path.write_text(
        json.dumps(row_payload["client_payload"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _run_runner(
    dispatch: Path,
    *,
    gateway_url: str,
    work: Path,
    evidence_path: Path,
    terminal: str,
    env: dict[str, str],
) -> dict[str, Any]:
    command = [
        "node",
        str(REPO / "runner/scripts/trace-e2e-runner.mjs"),
        "--dispatch",
        str(dispatch),
        "--state-dir",
        str(work / f"state-{terminal}-{time.time_ns()}"),
        "--object-root",
        str(work / f"objects-{terminal}-{time.time_ns()}"),
        "--gateway-url",
        gateway_url,
        "--gateway-token",
        SENSITIVE_SENTINEL,
        "--terminal",
        terminal,
        "--evidence",
        str(evidence_path),
    ]
    result = subprocess.run(
        command,
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"trace E2E runner failed ({terminal})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return json.loads(evidence_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("/tmp/m6-06-evidence.json"))
    parser.add_argument("--object-store", choices=("stub", "oss"), default="stub")
    parser.add_argument("--keep-workdir", action="store_true")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="m6-06-trace-e2e-"))
    api_port = free_port()
    api_base = f"http://127.0.0.1:{api_port}"
    stub: S3Stub | None = None
    gateway: subprocess.Popen[str] | None = None
    api: subprocess.Popen[str] | None = None
    worker: subprocess.Popen[str] | None = None
    api_log = (work / "api.log").open("w")
    worker_log = (work / "worker.log").open("w")
    gateway_log = (work / "gateway-requests.jsonl").open("w")

    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": env.get(
                "TRACE_E2E_DATABASE_URL", f"sqlite:///{work}/trace-e2e.db"
            ),
            "REDIS_URL": "",
            "AUTH_SESSION_SECRET": "m6-e2e-session-secret",
            "AUTH_BOOTSTRAP_ADMIN_EMAIL": ADMIN_EMAIL,
            "AUTH_BOOTSTRAP_ADMIN_PASSWORD": ADMIN_PASSWORD,
            "RUNNER_TASK_SECRET": "m6-e2e-runner-task-secret",
            "RUNNER_CALLBACK_BASE_URL": api_base,
            "RUNNER_HEARTBEAT_TOKEN": "",
            "WORKER_POLL_SECONDS": "0.2",
            "EVAL_MAX_CASES": "10",
            "GITHUB_TOKEN": "",
            "PATH": f"{work / 'claude-bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        }
    )
    if args.object_store == "stub":
        stub = S3Stub()
        env.update(
            {
                "S3_ENDPOINT": stub.start(),
                "S3_BUCKET": "m6-trace-e2e",
                "S3_ACCESS_KEY": "m6-e2e-access",
                "S3_SECRET_KEY": "m6-e2e-secret",
                "S3_REGION": "us-east-1",
                "S3_ADDRESSING_STYLE": "path",
            }
        )
    else:
        required = ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")
        missing = [name for name in required if not env.get(name)]
        if missing:
            raise RuntimeError(f"OSS mode requires secrets: {', '.join(missing)}")

    try:
        gateway = subprocess.Popen(
            [
                sys.executable,
                str(REPO / "tests/integration/m6_06_fake_anthropic_gateway.py"),
                "--port",
                str(free_port()),
                "--log",
                str(work / "gateway-requests.jsonl"),
            ],
            cwd=REPO,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=gateway_log,
        )
        assert gateway.stdout is not None
        gateway_url = json.loads(gateway.stdout.readline())["url"]
        wait_http(f"{gateway_url}/healthz")

        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=BACKEND,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [sys.executable, "-m", "app.seed"],
            cwd=BACKEND,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            cwd=BACKEND,
            env=env,
            stdout=api_log,
            stderr=subprocess.STDOUT,
        )
        wait_http(f"{api_base}/healthz")

        os.environ.update(env)
        sys.path.insert(0, str(BACKEND))
        from app.config import Settings, get_settings  # noqa: PLC0415
        from app.db import SessionLocal  # noqa: PLC0415
        from app.models import (  # noqa: PLC0415
            AuditLog,
            EvalCaseAttempt,
            EvalCaseRun,
            Outbox,
        )
        from app.models.trace import TraceFile, TraceSession, TraceSpan  # noqa: PLC0415
        from app.seed import ensure_default_scope  # noqa: PLC0415
        from app.services import evaluations, identity, trace_ingestion  # noqa: PLC0415
        from app.services.trace_parsers import ParserRegistry  # noqa: PLC0415
        from app.services.trace_parsers.claude_code import ClaudeCodeParser  # noqa: PLC0415

        get_settings.cache_clear()
        settings = Settings()
        with SessionLocal() as session:
            tenant, workspace, _project = ensure_default_scope(session)
            viewer = identity.upsert_member(
                session,
                email=VIEWER_EMAIL,
                password=VIEWER_PASSWORD,
                tenant=tenant,
                workspace=workspace,
                role=identity.ROLE_VIEWER,
                is_default=True,
            )
            run, _ = evaluations.create_synthetic_evaluation(
                session,
                name="M6-06 real Claude trace E2E",
                case_keys=["m6-trace-e2e-case"],
                shard_size=1,
                requested_concurrency=1,
                case_timeout_seconds=600,
                max_attempts=2,
                idempotency_key=f"m6-e2e-{time.time_ns()}",
                created_by=ADMIN_EMAIL,
                runtime_key="claude-code",
                runtime_version="2.1.278",
                settings=settings,
            )
            outbox = (
                session.query(Outbox)
                .filter(Outbox.aggregate_id == run.id)
                .order_by(Outbox.dispatch_key)
                .first()
            )
            if outbox is None:
                raise AssertionError("first dispatch outbox missing")
            dispatch_payload = json.loads(json.dumps(outbox.payload_json))
            case_run = (
                session.query(EvalCaseRun)
                .filter(EvalCaseRun.eval_run_id == run.id)
                .one()
            )
            run_id = run.id
            case_run_id = case_run.id
            viewer_id = viewer.id
            session.commit()

        dispatch_one = _read_dispatch(work, dispatch_payload, "dispatch-attempt-1.json")
        evidence_one = _run_runner(
            dispatch_one,
            gateway_url=gateway_url,
            work=work,
            evidence_path=work / "attempt-1.json",
            terminal="failed",
            env=env,
        )

        retry_payload: dict[str, Any] | None = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with SessionLocal() as session:
                retry = (
                    session.query(Outbox)
                    .filter(Outbox.aggregate_id == run_id)
                    .order_by(Outbox.dispatch_key.desc())
                    .all()
                )
                for row in retry:
                    if int((row.payload_json or {}).get("attempt") or 0) == 2:
                        retry_payload = json.loads(json.dumps(row.payload_json))
                        break
            if retry_payload:
                break
            time.sleep(0.2)
        if retry_payload is None:
            raise AssertionError("retry attempt dispatch was not created")

        dispatch_two = _read_dispatch(work, retry_payload, "dispatch-attempt-2.json")
        evidence_two = _run_runner(
            dispatch_two,
            gateway_url=gateway_url,
            work=work,
            evidence_path=work / "attempt-2.json",
            terminal="succeeded",
            env=env,
        )

        worker = subprocess.Popen(
            [sys.executable, "-m", "app.worker.main"],
            cwd=BACKEND,
            env=env,
            stdout=worker_log,
            stderr=subprocess.STDOUT,
        )

        admin = Api(api_base)
        admin.login(ADMIN_EMAIL, ADMIN_PASSWORD)
        viewer_api = Api(api_base)
        viewer_api.login(VIEWER_EMAIL, VIEWER_PASSWORD)
        anonymous = Api(api_base)

        trace_one: dict[str, Any] = {}
        trace_two: dict[str, Any] = {}
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status_one, body_one = admin.request(
                "GET", f"/api/v1/eval-case-runs/{case_run_id}/trace?attempt=1"
            )
            status_two, body_two = admin.request(
                "GET", f"/api/v1/eval-case-runs/{case_run_id}/trace?attempt=2"
            )
            if (
                status_one == 200
                and status_two == 200
                and body_one["trace_status"] == "ready"
                and body_two["trace_status"] == "ready"
                and body_one["stats"]["session_count"] == 3
                and body_two["stats"]["session_count"] == 3
            ):
                trace_one = body_one
                trace_two = body_two
                break
            time.sleep(0.5)
        if not trace_one or not trace_two:
            raise AssertionError("trace ingestion did not reach ready for both attempts")

        sessions_one = _flatten_sessions(trace_one["sessions"])
        sessions_two = _flatten_sessions(trace_two["sessions"])
        files_one = trace_one["files"]
        files_two = trace_two["files"]
        ids_one = {item["id"] for item in sessions_one}
        ids_two = {item["id"] for item in sessions_two}
        if ids_one & ids_two:
            raise AssertionError("attempt session IDs are not isolated")
        if {item["id"] for item in files_one} & {item["id"] for item in files_two}:
            raise AssertionError("attempt trace file IDs are not isolated")

        raw_file = next(
            item
            for item in files_two
            if item["artifact_kind"] == "trace" and item["status"] == "ready"
        )
        raw_status, raw_body, raw_headers = admin.request_with_headers(
            "POST", f"/api/v1/trace-files/{raw_file['id']}/download-url"
        )
        if raw_status != 200:
            raise AssertionError(f"raw download URL failed: {raw_status} {raw_body}")
        with urllib.request.urlopen(raw_body["url"], timeout=30) as response:
            raw_bytes = response.read()
            raw_http_status = response.status
        if len(raw_bytes) != raw_file["size"]:
            raise AssertionError("raw download size mismatch")

        root_session_id = trace_two["sessions"][0]["id"]
        viewer_status, viewer_body = viewer_api.request(
            "GET", f"/api/v1/trace-sessions/{root_session_id}/spans?limit=200"
        )
        viewer_spans = viewer_body.get("items", []) if isinstance(viewer_body, dict) else []
        viewer_span_text = json.dumps(viewer_body, ensure_ascii=False)
        xss_visible_as_text = "<script>window.__m6_xss=1</script>" in viewer_span_text
        viewer_thinking_hidden = all(
            span.get("content_hidden") is True
            for span in viewer_spans
            if span.get("kind") == "THINKING"
        )
        viewer_raw_status, _ = viewer_api.request(
            "POST", f"/api/v1/trace-files/{raw_file['id']}/download-url"
        )
        anonymous_status, _ = anonymous.request(
            "GET", f"/api/v1/eval-case-runs/{case_run_id}/trace"
        )
        unregistered_status, _ = admin.request(
            "POST", "/api/v1/trace-files/not-registered/download-url"
        )

        file_ids_before = {item["id"] for item in files_two}
        with SessionLocal() as session:
            attempt = session.query(EvalCaseAttempt).filter(
                EvalCaseAttempt.eval_case_run_id == case_run_id,
                EvalCaseAttempt.attempt_no == 2,
            ).one()
            sessions_before = session.query(TraceSession).filter(
                TraceSession.eval_case_attempt_id == attempt.id
            ).count()
            spans_before = (
                session.query(TraceSpan)
                .join(TraceSession, TraceSession.id == TraceSpan.session_id)
                .filter(TraceSession.eval_case_attempt_id == attempt.id)
                .count()
            )

            class NextParser(ClaudeCodeParser):
                parser_version = "claude-code-2.1.278-e2e-next"

            registry = ParserRegistry((ClaudeCodeParser(), NextParser()))
            replay = trace_ingestion.ingest_attempt(
                session,
                attempt,
                parser_registry=registry,
                parser_version=NextParser.parser_version,
                settings=settings,
            )
            sessions_after = session.query(TraceSession).filter(
                TraceSession.eval_case_attempt_id == attempt.id
            ).count()
            spans_after = (
                session.query(TraceSpan)
                .join(TraceSession, TraceSession.id == TraceSpan.session_id)
                .filter(TraceSession.eval_case_attempt_id == attempt.id)
                .count()
            )
            parser_versions = sorted(
                {
                    row.parser_version
                    for row in session.query(TraceFile).filter(
                        TraceFile.eval_case_attempt_id == attempt.id
                    )
                }
            )
            audit = (
                session.query(AuditLog)
                .filter(AuditLog.action == "trace.file.download_url")
                .order_by(AuditLog.created_at.desc())
                .first()
            )

        if replay["trace_status"] != "ready":
            raise AssertionError(f"parser replay failed: {replay}")
        if (sessions_before, spans_before) != (sessions_after, spans_after):
            raise AssertionError("parser replay duplicated or lost derived rows")
        if NextParser.parser_version not in parser_versions:
            raise AssertionError("upgraded parser version is not recorded")
        if audit is None:
            raise AssertionError("raw download audit row missing")

        flat_two = _flatten_sessions(trace_two["sessions"])
        children = [item for item in flat_two if item.get("parent_session_id")]
        evidence = {
            "schema_version": "1.0",
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "environment": {
                "runner": os.environ.get("RUNNER_NAME", "local-development"),
                "host": socket.gethostname(),
                "object_store": args.object_store,
                "database": "postgresql" if env["DATABASE_URL"].startswith("postgres") else "sqlite",
                "claude_code_version": "2.1.278",
            },
            "eval_run_id": run_id,
            "case_run_id": case_run_id,
            "runtime_key": "claude-code",
            "runtime_version": "2.1.278",
            "parser_version": "claude-code-2.1.278-v1",
            "capture_status": evidence_two["capture_status"],
            "root_session_id": evidence_two["root_session_id"],
            "sessions": flat_two,
            "spans": viewer_spans,
            "trace_files": [
                {
                    "id": item["id"],
                    "logical_path": item["logical_path"],
                    "status": item["status"],
                }
                for item in trace_two["files"]
            ],
            "raw_download": {
                "status": raw_http_status,
                "cache_control": raw_headers.get("cache-control"),
                "audit_action": audit.action,
                "size": len(raw_bytes),
            },
            "attempts": [
                {
                    "number": 1,
                    "trace_status": trace_one["trace_status"],
                    "session_count": trace_one["stats"]["session_count"],
                    "file_ids": [item["id"] for item in files_one],
                },
                {
                    "number": 2,
                    "trace_status": trace_two["trace_status"],
                    "session_count": trace_two["stats"]["session_count"],
                    "file_ids": [item["id"] for item in files_two],
                },
            ],
            "retry": {
                "attempt_1_terminal": evidence_one["terminal"],
                "attempt_2_terminal": evidence_two["terminal"],
                "isolated": not bool(ids_one & ids_two),
            },
            "duplicates": {
                "upload_callbacks_resent": True,
                "failure_callback_resent": True,
                "completion_callback_resent": True,
            },
            "parser_replay": {
                "replaced": replay.get("replaced", False),
                "sessions_before": sessions_before,
                "sessions_after": sessions_after,
                "spans_before": spans_before,
                "spans_after": spans_after,
                "original_file_ids_preserved": file_ids_before.issubset(
                    {item["id"] for item in trace_two["files"]}
                ),
                "parser_versions": parser_versions,
                "replay_parser_version": NextParser.parser_version,
            },
            "permissions": {
                "viewer_raw_status": viewer_raw_status,
                "viewer_span_lookup_status": viewer_status,
                "viewer_thinking_hidden": viewer_thinking_hidden,
                "anonymous_trace_status": anonymous_status,
                "unregistered_file_status": unregistered_status,
                "viewer_id": viewer_id,
                "viewer_span_lookup_error": (
                    viewer_body if isinstance(viewer_body, str) else "scoped"
                ),
            },
            "native_file_count": len(evidence_two["native_files"]),
            "child_count": len(children),
            "xss_content_marker": (
                "<script>window.__m6_xss=1</script>" if xss_visible_as_text else None
            ),
            "sensitive_scan": "passed",
        }

        raw_text = json.dumps(evidence, ensure_ascii=False)
        for forbidden in (
            SENSITIVE_SENTINEL,
            env.get("S3_SECRET_KEY", ""),
            env.get("RUNNER_TASK_SECRET", ""),
        ):
            if forbidden and forbidden in raw_text:
                raise AssertionError("sensitive value leaked into E2E evidence")
        if viewer_raw_status != 403:
            raise AssertionError(f"viewer raw status should be 403, got {viewer_raw_status}")
        if anonymous_status != 401:
            raise AssertionError(f"anonymous trace status should be 401, got {anonymous_status}")
        if unregistered_status != 404:
            raise AssertionError(
                f"unregistered file status should be 404, got {unregistered_status}"
            )

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
        validated = subprocess.run(
            ["node", str(REPO / "runner/lib/trace-e2e.mjs"), str(args.out)],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
        )
        if validated.returncode != 0:
            raise AssertionError(
                f"evidence assertion failed: {validated.stdout}{validated.stderr}"
            )
        print(json.dumps({"ok": True, "out": str(args.out), "evidence": evidence}))
        return 0
    finally:
        for process in (worker, api, gateway):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
        api_log.close()
        worker_log.close()
        gateway_log.close()
        if stub is not None:
            stub.stop()
        if not args.keep_workdir:
            import shutil

            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

"""M9 (#263) signed session-envelope endpoint.

Valid signatures return the envelope (identity, one-level childSessionIds,
NDJSON trajectory from the object store); wrong/expired signatures and
unknown sessions answer with stable codes; oversized trajectories fail with
413 instead of truncating.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from app.api import self_evolution as self_evolution_api
from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, TraceFile, TraceSession
from app.seed import ensure_default_scope
from app.services import self_evolution as swe
from fastapi.testclient import TestClient
from object_stub import StubObjectStore
from sqlalchemy.orm import Session

SIGNING_KEY = "test-signing-key"
MAIN_SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CHILD_SESSION = "ffffffff-1111-2222-3333-444444444444"
NDJSON = (
    b'{"type":"user","message":"start"}\n'
    b'{"type":"assistant","message":"work"}\n'
    b'{"type":"result","status":"ok"}\n'
)
CHILD_NDJSON = (
    b'{"type":"user","message":"child start"}\n'
    b'{"type":"result","status":"ok"}\n'
)


def _signed(
    run_id: str, session_id: str, *, exp: int | None = None, key: str = SIGNING_KEY
) -> dict:
    exp_value = exp if exp is not None else int(
        (datetime.now(UTC) + timedelta(seconds=60)).timestamp()
    )
    return {
        "run": run_id,
        "exp": str(exp_value),
        "sig": swe.session_signature(key, run_id, exp_value),
    }


@pytest.fixture
def trace_env(session: Session, client: TestClient, configure_settings):
    tenant, _workspace, project = ensure_default_scope(session)
    from app.services import evaluations as evaluation_service

    run, _ = evaluation_service.create_synthetic_evaluation(
        session,
        name="envelope-fixture",
        case_keys=["case-env"],
        idempotency_key="swe-envelope",
        created_by="agent",
        runtime_key="claude-code",
        settings=get_settings(),
        now=datetime.now(UTC),
    )
    case = session.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id).one()
    attempt = (
        session.query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case.id)
        .one()
    )
    with StubObjectStore() as store:
        configure_settings(
            SELF_EVO_SESSION_SIGNING_KEY=SIGNING_KEY,
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        main_file = TraceFile(
            tenant_id=tenant.id,
            eval_case_attempt_id=attempt.id,
            eval_case_run_id=case.id,
            runtime_key="claude-code",
            parser_version="claude-code-2.1.278-v1",
            artifact_kind="trace",
            object_key=f"traces/{MAIN_SESSION}.jsonl",
            sha256=hashlib.sha256(NDJSON).hexdigest(),
            size=len(NDJSON),
            status="ready",
        )
        child_file = TraceFile(
            tenant_id=tenant.id,
            eval_case_attempt_id=attempt.id,
            eval_case_run_id=case.id,
            runtime_key="claude-code",
            parser_version="claude-code-2.1.278-v1",
            artifact_kind="trace",
            object_key=f"traces/{CHILD_SESSION}.jsonl",
            sha256=hashlib.sha256(CHILD_NDJSON).hexdigest(),
            size=len(CHILD_NDJSON),
            status="ready",
        )
        session.add_all([main_file, child_file])
        session.flush()
        main = TraceSession(
            tenant_id=tenant.id,
            trace_file_id=main_file.id,
            eval_case_attempt_id=attempt.id,
            runtime_key="claude-code",
            parser_version="claude-code-2.1.278-v1",
            native_session_id=MAIN_SESSION,
            native_session_key=MAIN_SESSION,
            status="ready",
        )
        session.add(main)
        session.flush()
        session.add(
            TraceSession(
                tenant_id=tenant.id,
                trace_file_id=child_file.id,
                eval_case_attempt_id=attempt.id,
                runtime_key="claude-code",
                parser_version="claude-code-2.1.278-v1",
                native_session_id=CHILD_SESSION,
                native_session_key=CHILD_SESSION,
                parent_session_id=main.id,
                agent_id="research-agent",
                agent_name="research",
                status="ready",
            )
        )
        store.add_bytes(f"traces/{MAIN_SESSION}.jsonl", NDJSON)
        store.add_bytes(f"traces/{CHILD_SESSION}.jsonl", CHILD_NDJSON)
        session.commit()
        yield {"session": session, "store": store, "run_id": run.id}


def _error_code(response) -> str:
    return response.json()["detail"]["error"]["code"]


def test_valid_signature_returns_envelope(trace_env, client: TestClient) -> None:
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(trace_env["run_id"], MAIN_SESSION),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schemaVersion"] == 1
    assert body["sessionId"] == MAIN_SESSION
    assert body["runtime"] == "claude-code"
    assert body["runtimeVersion"] == "claude-code-2.1.278-v1"
    assert body["agentKind"] == "main"
    assert body["parentSessionId"] is None
    assert body["childSessionIds"] == [CHILD_SESSION]
    assert body["agentMeta"] is None
    assert body["trajectory"]["format"] == "ndjson"
    assert body["trajectory"]["lineCount"] == 3
    assert body["trajectory"]["content"] == NDJSON.decode("utf-8").rstrip("\n")
    assert body["updatedAt"]


def test_child_session_envelope_reports_parent(trace_env, client: TestClient) -> None:
    response = client.get(
        f"/api/v1/self-evolution/sessions/{CHILD_SESSION}",
        params=_signed(trace_env["run_id"], CHILD_SESSION),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agentKind"] == "subagent"
    assert body["parentSessionId"] == MAIN_SESSION
    assert body["childSessionIds"] == []
    # Only keys inside harness-evolution's closed ClaudeAgentMeta vocabulary
    # survive; agent_name maps to ``name``.
    assert body["agentMeta"] == {"name": "research"}


def test_wrong_signature_is_rejected(trace_env, client: TestClient) -> None:
    params = _signed(trace_env["run_id"], MAIN_SESSION)
    params["sig"] = "0" * 64
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}", params=params
    )
    assert response.status_code == 401
    assert _error_code(response) == "self_evolution_signature_invalid"

    # The grant is prefix+run-scoped on purpose: harness-evolution substitutes
    # child session ids into the same template during BFS traversal, so a
    # signature issued alongside a child id must verify on the root too —
    # but only within the same eval run.
    cross = _signed(trace_env["run_id"], CHILD_SESSION)
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}", params=cross
    )
    assert response.status_code == 200

    # A signature (or template) from another eval run must not open this run's
    # sessions: the endpoint answers with the same stable 401 code.
    other_run_id = "0f0e0d0c-0b0a-0908-0706-050403020100"
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(other_run_id, MAIN_SESSION),
    )
    assert response.status_code == 401
    assert _error_code(response) == "self_evolution_signature_invalid"


def test_expired_signature_is_rejected(trace_env, client: TestClient) -> None:
    expired = int((datetime.now(UTC) - timedelta(seconds=10)).timestamp())
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(trace_env["run_id"], MAIN_SESSION, exp=expired),
    )
    assert response.status_code == 401
    assert _error_code(response) == "self_evolution_signature_expired"


def test_malformed_query_and_session_id_are_rejected(trace_env, client: TestClient) -> None:
    base = f"/api/v1/self-evolution/sessions/{MAIN_SESSION}"
    response = client.get(base, params={"run": trace_env["run_id"], "exp": "abc", "sig": "x"})
    assert response.status_code == 401
    assert _error_code(response) == "self_evolution_signature_invalid"

    response = client.get(base)
    assert response.status_code == 401

    # run is required: a signed grant without the run scope is invalid.
    signed = _signed(trace_env["run_id"], MAIN_SESSION)
    signed.pop("run")
    response = client.get(base, params=signed)
    assert response.status_code == 401
    assert _error_code(response) == "self_evolution_signature_invalid"

    bad_id = "has spaces and/slashes"
    response = client.get(
        f"/api/v1/self-evolution/sessions/{bad_id}",
        params=_signed(trace_env["run_id"], bad_id),
    )
    assert response.status_code in (401, 404)


def test_unknown_session_is_404(trace_env, client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    response = client.get(
        f"/api/v1/self-evolution/sessions/{missing}",
        params=_signed(trace_env["run_id"], missing),
    )
    assert response.status_code == 404
    assert _error_code(response) == "self_evolution_session_not_found"


def test_unsupported_runtime_is_404(trace_env, session: Session, client: TestClient) -> None:
    generic = "99999999-9999-9999-9999-999999999999"
    main = (
        session.query(TraceSession)
        .filter(TraceSession.native_session_id == MAIN_SESSION)
        .one()
    )
    trace_file = TraceFile(
        tenant_id=main.tenant_id,
        eval_case_attempt_id=main.eval_case_attempt_id,
        eval_case_run_id=session.query(EvalCaseRun).first().id,
        runtime_key="codex",
        parser_version="raw-jsonl-1",
        artifact_kind="trace",
        object_key=f"traces/{generic}.jsonl",
        sha256="0" * 64,
        size=2,
        status="ready",
    )
    session.add(trace_file)
    session.flush()
    session.add(
        TraceSession(
            tenant_id=trace_file.tenant_id,
            trace_file_id=trace_file.id,
            eval_case_attempt_id=trace_file.eval_case_attempt_id,
            runtime_key="codex",
            parser_version="raw-jsonl-1",
            native_session_id=generic,
            native_session_key=generic,
            status="ready",
        )
    )
    session.commit()
    response = client.get(
        f"/api/v1/self-evolution/sessions/{generic}",
        params=_signed(trace_env["run_id"], generic),
    )
    assert response.status_code == 404
    assert _error_code(response) == "self_evolution_runtime_unsupported"


def test_oversized_line_is_rejected_without_truncation(
    trace_env, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    big_line = b"x" * (self_evolution_api.MAX_LINE_BYTES + 1)
    payload = big_line + b"\n" + b'{"ok":true}' + b"\n"
    trace_env["store"].add_bytes(f"traces/{MAIN_SESSION}.jsonl", payload)
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(trace_env["run_id"], MAIN_SESSION),
    )
    assert response.status_code == 413
    assert _error_code(response) == "self_evolution_trajectory_line_too_long"


def test_total_and_line_count_limits_are_enforced(
    trace_env, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Total-byte ceiling (monkeypatched small): the object exceeds it.
    monkeypatch.setattr(self_evolution_api, "MAX_TOTAL_BYTES", 4)
    trace_env["store"].add_bytes(f"traces/{MAIN_SESSION}.jsonl", b"a" * 10)
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(trace_env["run_id"], MAIN_SESSION),
    )
    assert response.status_code == 413
    assert _error_code(response) == "self_evolution_trajectory_too_large"

    # Line-count ceiling.
    monkeypatch.setattr(self_evolution_api, "MAX_TOTAL_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(self_evolution_api, "MAX_LINES", 2)
    trace_env["store"].add_bytes(
        f"traces/{MAIN_SESSION}.jsonl", b'{"a":1}\n{"b":2}\n{"c":3}\n'
    )
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(trace_env["run_id"], MAIN_SESSION),
    )
    assert response.status_code == 413
    assert _error_code(response) == "self_evolution_trajectory_too_many_lines"


def test_missing_signing_key_fails_closed(trace_env, client: TestClient, configure_settings) -> None:
    configure_settings(SELF_EVO_SESSION_SIGNING_KEY="")
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(trace_env["run_id"], MAIN_SESSION),
    )
    assert response.status_code == 503
    assert _error_code(response) == "self_evolution_not_configured"

"""M9 (#263, #338) signed session-envelope endpoint.

Valid signatures return the envelope (identity, one-level childSessionIds,
NDJSON trajectory from the object store); wrong/expired signatures and
unknown sessions answer with stable codes; oversized trajectories fail with
413 instead of truncating.

The fixture mirrors the real Claude Code shape behind #338: the root and every
subagent share one native ``sessionId``, a subagent can sit below another
subagent, and an unresolved child is ``status=unlinked`` with no parent. Root
requests keep the native root id while subagent entries address the platform
``TraceSession.id``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.api import self_evolution as self_evolution_api
from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalRun, TraceFile, TraceSession
from app.seed import ensure_default_scope
from app.services import self_evolution as swe
from fastapi.testclient import TestClient
from object_stub import StubObjectStore
from sqlalchemy.orm import Session

SIGNING_KEY = "test-signing-key"
MAIN_SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ORPHAN_SESSION = "bbbbbbbb-1111-2222-3333-444444444444"
SINGLE_SESSION = "0f66d27f-2222-3333-4444-555555555555"
PARSER_VERSION = "claude-code-2.1.278-v1"
ROOT_NDJSON = (
    b'{"type":"user","message":"root start"}\n'
    b'{"type":"assistant","message":"delegate"}\n'
    b'{"type":"result","status":"ok"}\n'
)
CHILD_NDJSON = (
    b'{"type":"user","message":"child start"}\n'
    b'{"type":"result","status":"ok"}\n'
)
GRANDCHILD_NDJSON = b'{"type":"result","status":"ok","message":"grandchild"}\n'
REVIEWER_NDJSON = b'{"type":"result","status":"ok","message":"reviewer"}\n'
UNLINKED_NDJSON = (
    b'{"type":"user","message":"unlinked start"}\n'
    b'{"type":"result","status":"ok"}\n'
)
ORPHAN_NDJSON = b'{"type":"result","status":"ok","message":"orphan"}\n'
OTHER_ROOT_NDJSON = b'{"type":"result","status":"ok","message":"other run root"}\n'
SINGLE_NDJSON = b'{"type":"result","status":"ok","message":"single agent"}\n'


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


def _create_run(
    session: Session, *, name: str, idempotency_key: str
) -> tuple[EvalRun, EvalCaseRun, EvalCaseAttempt]:
    """Create one synthetic evaluation run with a single case attempt."""

    from app.services import evaluations as evaluation_service

    run, _ = evaluation_service.create_synthetic_evaluation(
        session,
        name=name,
        case_keys=["case-env"],
        idempotency_key=idempotency_key,
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
    return run, case, attempt


def _add_session(
    session: Session,
    store: StubObjectStore,
    *,
    tenant_id: str,
    case: EvalCaseRun,
    attempt: EvalCaseAttempt,
    native_id: str,
    payload: bytes,
    ordinal: int,
    parent: TraceSession | None = None,
    status: str = "ready",
    agent_id: str | None = None,
    agent_name: str | None = None,
    parser_version: str = PARSER_VERSION,
    runtime_key: str = "claude-code",
) -> TraceSession:
    object_key = f"traces/{uuid4().hex}.jsonl"
    trace_file = TraceFile(
        tenant_id=tenant_id,
        eval_case_attempt_id=attempt.id,
        eval_case_run_id=case.id,
        runtime_key=runtime_key,
        parser_version=parser_version,
        artifact_kind="trace",
        object_key=object_key,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        status="ready",
    )
    session.add(trace_file)
    session.flush()
    row = TraceSession(
        tenant_id=tenant_id,
        trace_file_id=trace_file.id,
        eval_case_attempt_id=attempt.id,
        runtime_key=runtime_key,
        parser_version=parser_version,
        native_session_id=native_id,
        native_session_key=f"{native_id}:{agent_id or 'root'}",
        parent_session_id=parent.id if parent is not None else None,
        agent_id=agent_id,
        agent_name=agent_name,
        status=status,
        ordinal=ordinal,
    )
    session.add(row)
    session.flush()
    store.add_bytes(object_key, payload)
    return row


@pytest.fixture
def trace_env(session: Session, client: TestClient, configure_settings):
    tenant, _workspace, _project = ensure_default_scope(session)
    run, case, attempt = _create_run(
        session, name="envelope-fixture", idempotency_key="swe-envelope"
    )
    with StubObjectStore() as store:
        configure_settings(
            SELF_EVO_SESSION_SIGNING_KEY=SIGNING_KEY,
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        root = _add_session(
            session,
            store,
            tenant_id=tenant.id,
            case=case,
            attempt=attempt,
            native_id=MAIN_SESSION,
            payload=ROOT_NDJSON,
            ordinal=0,
        )
        child = _add_session(
            session,
            store,
            tenant_id=tenant.id,
            case=case,
            attempt=attempt,
            native_id=MAIN_SESSION,
            payload=CHILD_NDJSON,
            ordinal=1,
            parent=root,
            agent_id="ad5308f2786ef2936",
            agent_name="code-generator",
        )
        reviewer = _add_session(
            session,
            store,
            tenant_id=tenant.id,
            case=case,
            attempt=attempt,
            native_id=MAIN_SESSION,
            payload=REVIEWER_NDJSON,
            ordinal=2,
            parent=root,
            agent_id="a02a17da8e2321430",
            agent_name="spec-reviewer",
        )
        grandchild = _add_session(
            session,
            store,
            tenant_id=tenant.id,
            case=case,
            attempt=attempt,
            native_id=MAIN_SESSION,
            payload=GRANDCHILD_NDJSON,
            ordinal=3,
            parent=child,
            agent_id="d3f87d73b8ed4c11",
            agent_name="code-quality-reviewer",
        )
        unlinked = _add_session(
            session,
            store,
            tenant_id=tenant.id,
            case=case,
            attempt=attempt,
            native_id=MAIN_SESSION,
            payload=UNLINKED_NDJSON,
            ordinal=4,
            status="unlinked",
            agent_id="ac44856bbe582c3e0",
            agent_name="unlinked-worker",
        )
        orphan = _add_session(
            session,
            store,
            tenant_id=tenant.id,
            case=case,
            attempt=attempt,
            native_id=ORPHAN_SESSION,
            payload=ORPHAN_NDJSON,
            ordinal=5,
            status="unlinked",
            agent_id="aee12466b14d6ef6f",
            agent_name="orphan-worker",
        )
        session.commit()
        root_file = session.get(TraceFile, root.trace_file_id)
        yield {
            "session": session,
            "store": store,
            "tenant": tenant,
            "run_id": run.id,
            "case": case,
            "attempt": attempt,
            "root_id": root.id,
            "root_key": root_file.object_key,
            "child_ids": [child.id, reviewer.id],
            "child_id": child.id,
            "grandchild_id": grandchild.id,
            "unlinked_id": unlinked.id,
            "orphan_id": orphan.id,
        }


def _error_code(response) -> str:
    return response.json()["detail"]["error"]["code"]


def test_root_with_shared_native_id_returns_main_and_platform_children(
    trace_env, client: TestClient
) -> None:
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(trace_env["run_id"], MAIN_SESSION),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schemaVersion"] == 1
    assert body["sessionId"] == MAIN_SESSION
    assert body["runtime"] == "claude-code"
    assert body["runtimeVersion"] == PARSER_VERSION
    assert body["agentKind"] == "main"
    assert body["parentSessionId"] is None
    # childSessionIds are platform session ids, not the shared native id, and
    # follow ordinal order; unlinked rows never appear.
    assert body["childSessionIds"] == trace_env["child_ids"]
    assert len(set(body["childSessionIds"])) == 2
    assert MAIN_SESSION not in body["childSessionIds"]
    assert body["agentMeta"] is None
    assert body["trajectory"]["format"] == "ndjson"
    assert body["trajectory"]["lineCount"] == 3
    assert body["trajectory"]["content"] == ROOT_NDJSON.decode("utf-8").rstrip("\n")
    assert body["updatedAt"]


def test_child_session_envelope_uses_platform_id_and_root_native_parent(
    trace_env, client: TestClient
) -> None:
    child_id = trace_env["child_id"]
    response = client.get(
        f"/api/v1/self-evolution/sessions/{child_id}",
        params=_signed(trace_env["run_id"], child_id),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sessionId"] == child_id
    assert body["agentKind"] == "subagent"
    # The direct parent is the root, so the envelope keeps the frozen native
    # root id that HE already requested.
    assert body["parentSessionId"] == MAIN_SESSION
    assert body["childSessionIds"] == [trace_env["grandchild_id"]]
    assert body["agentMeta"] == {"name": "code-generator"}
    assert body["trajectory"]["lineCount"] == 2


def test_depth_two_parent_uses_parent_platform_id(trace_env, client: TestClient) -> None:
    grandchild_id = trace_env["grandchild_id"]
    response = client.get(
        f"/api/v1/self-evolution/sessions/{grandchild_id}",
        params=_signed(trace_env["run_id"], grandchild_id),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agentKind"] == "subagent"
    assert body["parentSessionId"] == trace_env["child_id"]
    assert body["childSessionIds"] == []


def test_he_bfs_pull_traverses_root_children_and_grandchild(
    trace_env, client: TestClient
) -> None:
    """Simulate harness-evolution's BFS: every promised id must be fetchable."""

    run_id = trace_env["run_id"]
    envelopes: dict[str, dict] = {}
    queue = [MAIN_SESSION]
    while queue:
        session_id = queue.pop(0)
        if session_id in envelopes:
            continue
        response = client.get(
            f"/api/v1/self-evolution/sessions/{session_id}",
            params=_signed(run_id, session_id),
        )
        assert response.status_code == 200, (session_id, response.text)
        body = response.json()
        assert body["sessionId"] == session_id
        envelopes[session_id] = body
        for child_id in body["childSessionIds"]:
            assert child_id not in envelopes
            queue.append(child_id)

    assert set(envelopes) == {
        MAIN_SESSION,
        trace_env["child_id"],
        trace_env["child_ids"][1],
        trace_env["grandchild_id"],
    }
    assert envelopes[MAIN_SESSION]["agentKind"] == "main"
    assert envelopes[MAIN_SESSION]["parentSessionId"] is None
    assert envelopes[trace_env["child_id"]]["parentSessionId"] == MAIN_SESSION
    assert (
        envelopes[trace_env["grandchild_id"]]["parentSessionId"]
        == trace_env["child_id"]
    )


def test_unlinked_sessions_are_never_returned_as_main(
    trace_env, client: TestClient
) -> None:
    # Addressing the unlinked row by its platform id is an explicit miss.
    response = client.get(
        f"/api/v1/self-evolution/sessions/{trace_env['unlinked_id']}",
        params=_signed(trace_env["run_id"], trace_env["unlinked_id"]),
    )
    assert response.status_code == 404
    assert _error_code(response) == "self_evolution_session_not_found"

    # Its native id (or an orphan native id with only unlinked rows) must not
    # fall back to the subagent file; the endpoint answers 404, not main.
    response = client.get(
        f"/api/v1/self-evolution/sessions/{ORPHAN_SESSION}",
        params=_signed(trace_env["run_id"], ORPHAN_SESSION),
    )
    assert response.status_code == 404
    assert _error_code(response) == "self_evolution_session_not_found"


def test_cross_run_native_id_stays_inside_the_signed_run(
    trace_env, session: Session, client: TestClient
) -> None:
    tenant = trace_env["tenant"]
    other_run, other_case, other_attempt = _create_run(
        session, name="envelope-fixture-other", idempotency_key="swe-envelope-other"
    )
    other_root = _add_session(
        session,
        trace_env["store"],
        tenant_id=tenant.id,
        case=other_case,
        attempt=other_attempt,
        native_id=MAIN_SESSION,
        payload=OTHER_ROOT_NDJSON,
        ordinal=0,
    )
    other_child = _add_session(
        session,
        trace_env["store"],
        tenant_id=tenant.id,
        case=other_case,
        attempt=other_attempt,
        native_id=MAIN_SESSION,
        payload=GRANDCHILD_NDJSON,
        ordinal=1,
        parent=other_root,
        agent_id="ffffffffffffffff",
        agent_name="other-agent",
    )
    session.commit()

    # The same native id exists in two runs and the other run's row is newer;
    # the signed run must win instead of returning 401 or the other envelope.
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(trace_env["run_id"], MAIN_SESSION),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["childSessionIds"] == trace_env["child_ids"]
    assert body["trajectory"]["content"] == ROOT_NDJSON.decode("utf-8").rstrip("\n")

    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(other_run.id, MAIN_SESSION),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["childSessionIds"] == [other_child.id]
    assert body["trajectory"]["content"] == OTHER_ROOT_NDJSON.decode("utf-8").rstrip("\n")

    # A child id from another run is not reachable with this run's template.
    response = client.get(
        f"/api/v1/self-evolution/sessions/{other_child.id}",
        params=_signed(trace_env["run_id"], other_child.id),
    )
    assert response.status_code == 401
    assert _error_code(response) == "self_evolution_signature_invalid"


def test_single_agent_run_keeps_empty_children(
    trace_env, session: Session, client: TestClient
) -> None:
    single_run, single_case, single_attempt = _create_run(
        session, name="envelope-fixture-single", idempotency_key="swe-envelope-single"
    )
    _add_session(
        session,
        trace_env["store"],
        tenant_id=trace_env["tenant"].id,
        case=single_case,
        attempt=single_attempt,
        native_id=SINGLE_SESSION,
        payload=SINGLE_NDJSON,
        ordinal=0,
    )
    session.commit()

    response = client.get(
        f"/api/v1/self-evolution/sessions/{SINGLE_SESSION}",
        params=_signed(single_run.id, SINGLE_SESSION),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agentKind"] == "main"
    assert body["parentSessionId"] is None
    assert body["childSessionIds"] == []
    assert body["trajectory"]["lineCount"] == 1


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
    cross = _signed(trace_env["run_id"], trace_env["child_id"])
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


def test_unsupported_runtime_is_404(
    trace_env, session: Session, client: TestClient
) -> None:
    generic = "99999999-9999-9999-9999-999999999999"
    _add_session(
        session,
        trace_env["store"],
        tenant_id=trace_env["tenant"].id,
        case=trace_env["case"],
        attempt=trace_env["attempt"],
        native_id=generic,
        payload=b'{"ok":true}\n',
        ordinal=6,
        runtime_key="codex",
        parser_version="raw-jsonl-1",
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
    # Overwrite the root object without touching the database row.
    trace_env["store"].add_bytes(trace_env["root_key"], payload)
    response = client.get(
        f"/api/v1/self-evolution/sessions/{MAIN_SESSION}",
        params=_signed(trace_env["run_id"], MAIN_SESSION),
    )
    assert response.status_code == 413
    assert _error_code(response) == "self_evolution_trajectory_line_too_long"


def test_total_and_line_count_limits_are_enforced(
    trace_env, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_key = trace_env["root_key"]
    # Total-byte ceiling (monkeypatched small): the object exceeds it.
    monkeypatch.setattr(self_evolution_api, "MAX_TOTAL_BYTES", 4)
    trace_env["store"].add_bytes(root_key, b"a" * 10)
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
        root_key, b'{"a":1}\n{"b":2}\n{"c":3}\n'
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

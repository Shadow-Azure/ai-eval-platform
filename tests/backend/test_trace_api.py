"""M6-04: attempt-scoped trace queries and controlled raw access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from app.defaults import DEFAULT_TENANT_ID
from app.models import (
    AuditLog,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    Project,
    Tenant,
    TraceFile,
    TraceSession,
    TraceSpan,
    User,
    Workspace,
)
from app.models.base import new_id
from app.services import identity
from conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
from fastapi.testclient import TestClient
from object_stub import StubObjectStore
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from trace_ingest_helpers import create_env


def _login(client: TestClient, email: str, password: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text


def _add_viewer(session: Session) -> User:
    tenant = session.get(Tenant, str(DEFAULT_TENANT_ID))
    workspace = session.scalar(
        select(Workspace).where(Workspace.tenant_id == tenant.id)
    )
    user = identity.upsert_member(
        session,
        email=TEST_VIEWER_EMAIL,
        password=TEST_VIEWER_PASSWORD,
        tenant=tenant,
        workspace=workspace,
        role=identity.ROLE_VIEWER,
        is_default=True,
    )
    session.commit()
    return user


@dataclass
class TraceSeed:
    file: TraceFile
    root: TraceSession
    child: TraceSession


def _seed_trace(
    session: Session,
    env: dict,
    *,
    attempt: EvalCaseAttempt | None = None,
    parser_version: str = "claude-code-2.1.278-v1",
) -> TraceSeed:
    attempt = attempt or env["attempt"]
    object_key = f"evaluations/{env['run'].id}/trace/{attempt.id}/native.jsonl"
    env["store"].add_bytes(object_key, b'{"native":true}\n')
    env["store"].add_bytes("trace-content/private-thinking", b"full private content")
    file = TraceFile(
        tenant_id=str(DEFAULT_TENANT_ID),
        eval_case_attempt_id=attempt.id,
        eval_case_run_id=env["case"].id,
        runtime_key="claude-code",
        runtime_version="2.1.278",
        native_format="claude-code.session+jsonl",
        format_version="1",
        parser_version=parser_version,
        logical_path=f"native/projects/-testbed/{attempt.attempt_no}.jsonl",
        artifact_kind="trace",
        object_key=object_key,
        sha256="a" * 64,
        size=16,
        detected_format="jsonl",
        status="ready",
        ordinal=0,
    )
    session.add(file)
    session.flush()
    root = TraceSession(
        tenant_id=str(DEFAULT_TENANT_ID),
        trace_file_id=file.id,
        eval_case_attempt_id=attempt.id,
        runtime_key="claude-code",
        parser_version=parser_version,
        native_session_id="session-root",
        native_session_key="session-root:root",
        root_session_id="session-root",
        agent_id="root",
        agent_name="main",
        status="ready",
        relation_evidence_json={"confidence": "authoritative", "type": "root"},
        metadata_json={"agent_links": []},
        started_at=datetime(2026, 11, 11, tzinfo=UTC),
        ended_at=datetime(2026, 11, 11, 0, 0, 10, tzinfo=UTC),
        ordinal=0,
    )
    session.add(root)
    session.flush()
    child = TraceSession(
        tenant_id=str(DEFAULT_TENANT_ID),
        trace_file_id=file.id,
        eval_case_attempt_id=attempt.id,
        runtime_key="claude-code",
        parser_version=parser_version,
        native_session_id="session-root",
        native_session_key="session-root:agent-alpha",
        root_session_id="session-root",
        parent_session_id=root.id,
        agent_id="agent-alpha",
        agent_name="reviewer",
        status="ready",
        relation_evidence_json={
            "confidence": "authoritative",
            "type": "claude_code.agent_tool_call",
            "tool_use_id": "call-alpha",
        },
        metadata_json={},
        started_at=datetime(2026, 11, 11, 0, 0, 2, tzinfo=UTC),
        ended_at=datetime(2026, 11, 11, 0, 0, 8, tzinfo=UTC),
        ordinal=1,
    )
    session.add(child)
    session.flush()
    spans = [
        TraceSpan(
            tenant_id=str(DEFAULT_TENANT_ID),
            session_id=root.id,
            native_event_id="root-message",
            ordinal=0,
            kind="MESSAGE",
            name="user",
            started_at=datetime(2026, 11, 11, tzinfo=UTC),
            attributes_json={"gen_ai.operation.name": "chat"},
            input_tokens=10,
            output_tokens=2,
            content_preview="hello",
        ),
        TraceSpan(
            tenant_id=str(DEFAULT_TENANT_ID),
            session_id=root.id,
            native_event_id="root-tool",
            ordinal=1,
            kind="TOOL",
            name="Agent",
            started_at=datetime(2026, 11, 11, 0, 0, 1, tzinfo=UTC),
            ended_at=datetime(2026, 11, 11, 0, 0, 2, tzinfo=UTC),
            duration_ms=1000,
            attributes_json={
                "gen_ai.tool.call.id": "call-alpha",
                "authorization": "Bearer must-not-leak",
                "gen_ai.usage.input_tokens": 10,
            },
            content_preview="spawn reviewer",
        ),
        TraceSpan(
            tenant_id=str(DEFAULT_TENANT_ID),
            session_id=child.id,
            native_event_id="child-thinking",
            ordinal=0,
            kind="THINKING",
            name="reasoning",
            started_at=datetime(2026, 11, 11, 0, 0, 3, tzinfo=UTC),
            attributes_json={
                "gen_ai.agent.name": "reviewer",
                "api_key": "secret-key",
            },
            content_preview="private chain of thought",
            content_object_key="trace-content/private-thinking",
        ),
        TraceSpan(
            tenant_id=str(DEFAULT_TENANT_ID),
            session_id=child.id,
            native_event_id="child-message",
            ordinal=1,
            kind="MESSAGE",
            name="assistant",
            started_at=datetime(2026, 11, 11, 0, 0, 4, tzinfo=UTC),
            attributes_json={},
            content_preview="review complete",
        ),
    ]
    session.add_all(spans)
    env["case"].trace_status = "ready"
    env["case"].latest_attempt_no = attempt.attempt_no
    attempt.status = "succeeded"
    session.commit()
    return TraceSeed(file=file, root=root, child=child)


def _add_attempt(
    session: Session,
    env: dict,
    *,
    attempt_no: int,
) -> EvalCaseAttempt:
    attempt = EvalCaseAttempt(
        tenant_id=str(DEFAULT_TENANT_ID),
        eval_case_run_id=env["case"].id,
        attempt_no=attempt_no,
        status="succeeded",
    )
    session.add(attempt)
    session.commit()
    return attempt


@pytest.fixture
def trace_api_env(client: TestClient, session: Session, configure_settings):
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="access-id",
            S3_SECRET_KEY="must-not-leak-secret",
        )
        env = create_env(session, store, runtime_key="claude-code")
        seeded = _seed_trace(session, env)
        yield {**env, "seeded": seeded}


def test_attempt_trace_returns_session_tree_without_object_keys(
    client: TestClient, trace_api_env
) -> None:
    case = trace_api_env["case"]

    response = client.get(f"/api/v1/eval-case-runs/{case.id}/trace?attempt=1")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["case_run_id"] == case.id
    assert body["attempt"]["attempt_no"] == 1
    assert body["trace_status"] == "ready"
    assert body["stats"]["session_count"] == 2
    assert body["stats"]["span_count"] == 4
    assert body["sessions"][0]["agent_name"] == "main"
    assert body["sessions"][0]["children"][0]["agent_name"] == "reviewer"
    assert body["sessions"][0]["children"][0]["relation_evidence"]["tool_use_id"] == "call-alpha"
    assert "object_key" not in response.text
    assert "must-not-leak-secret" not in response.text


def test_spans_use_stable_keyset_cursor_and_reject_cross_session_cursor(
    client: TestClient, trace_api_env
) -> None:
    child = trace_api_env["seeded"].child

    first = client.get(f"/api/v1/trace-sessions/{child.id}/spans?limit=1")

    assert first.status_code == 200, first.text
    first_body = first.json()
    assert [item["ordinal"] for item in first_body["items"]] == [0]
    assert first_body["page_info"]["has_more"] is True
    cursor = first_body["page_info"]["next_cursor"]
    assert cursor

    second = client.get(
        f"/api/v1/trace-sessions/{child.id}/spans",
        params={"limit": 1, "cursor": cursor},
    )
    assert second.status_code == 200, second.text
    assert [item["ordinal"] for item in second.json()["items"]] == [1]
    assert second.json()["page_info"]["has_more"] is False

    root = trace_api_env["seeded"].root
    wrong_scope = client.get(
        f"/api/v1/trace-sessions/{root.id}/spans",
        params={"cursor": cursor},
    )
    assert wrong_scope.status_code == 422
    assert wrong_scope.json()["detail"]["error"]["code"] == "invalid_cursor"


def test_switching_attempt_never_reuses_sessions_or_spans(
    client: TestClient, session: Session, trace_api_env
) -> None:
    first_seed = trace_api_env["seeded"]
    attempt_two = _add_attempt(session, trace_api_env, attempt_no=2)
    second_seed = _seed_trace(
        session,
        trace_api_env,
        attempt=attempt_two,
        parser_version="claude-code-2.1.278-v2",
    )

    first = client.get(
        f"/api/v1/eval-case-runs/{trace_api_env['case'].id}/trace?attempt=1"
    )
    second = client.get(
        f"/api/v1/eval-case-runs/{trace_api_env['case'].id}/trace?attempt=2"
    )
    latest = client.get(
        f"/api/v1/eval-case-runs/{trace_api_env['case'].id}/trace"
    )

    assert first.status_code == second.status_code == latest.status_code == 200
    assert first.json()["files"][0]["id"] == first_seed.file.id
    assert second.json()["files"][0]["id"] == second_seed.file.id
    assert latest.json()["attempt"]["attempt_no"] == 2
    assert first.json()["sessions"][0]["id"] not in {
        item["id"] for item in second.json()["sessions"]
    }


def test_viewer_span_detail_hides_thinking_and_sensitive_attributes(
    client: TestClient, session: Session, trace_api_env
) -> None:
    child = trace_api_env["seeded"].child
    first_page = client.get(f"/api/v1/trace-sessions/{child.id}/spans?limit=10")
    thinking_id = first_page.json()["items"][0]["id"]
    _add_viewer(session)
    _login(client, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD)

    response = client.get(f"/api/v1/trace-spans/{thinking_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "THINKING"
    assert body["content_preview"] is None
    assert body["content_hidden"] is True
    assert body["hidden_reason"] == "thinking_requires_admin"
    assert body["content_available"] is True
    assert "content_object_key" not in response.text
    assert "private chain of thought" not in response.text
    assert body["attributes"]["api_key"] == "[REDACTED]"
    assert "secret-key" not in response.text

    root = trace_api_env["seeded"].root
    root_page = client.get(f"/api/v1/trace-sessions/{root.id}/spans?limit=10")
    root_tool = next(
        item for item in root_page.json()["items"] if item["kind"] == "TOOL"
    )
    assert root_tool["attributes"]["gen_ai.usage.input_tokens"] == 10
    assert root_tool["attributes"]["authorization"] == "[REDACTED]"


def test_raw_file_download_requires_admin_writes_audit_and_is_short_lived(
    client: TestClient, session: Session, trace_api_env
) -> None:
    file = trace_api_env["seeded"].file
    session.query(AuditLog).filter(
        AuditLog.action == "trace.file.download_url"
    ).delete()
    session.commit()

    response = client.post(f"/api/v1/trace-files/{file.id}/download-url")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["file"]["id"] == file.id
    assert "object_key" not in body["file"]
    assert "X-Amz-Expires=300" in body["url"]
    assert "must-not-leak-secret" not in response.text
    expires_at = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    assert timedelta(seconds=250) <= expires_at - datetime.now(UTC) <= timedelta(
        seconds=301
    )
    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "trace.file.download_url")
    )
    assert audit is not None
    assert audit.target_type == "trace_file"
    assert audit.target_id == file.id
    assert audit.metadata_json["result"] == "issued"


def test_trace_queries_require_session_and_viewer_cannot_download_raw(
    client: TestClient, session: Session, trace_api_env
) -> None:
    file = trace_api_env["seeded"].file
    _add_viewer(session)
    _login(client, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD)

    forbidden = client.post(f"/api/v1/trace-files/{file.id}/download-url")
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["error"]["code"] == "forbidden"
    assert (
        session.scalar(
            select(AuditLog).where(
                AuditLog.action == "trace.file.download_url"
            )
        )
        is None
    )

    client.cookies.clear()
    anonymous = client.get(
        f"/api/v1/eval-case-runs/{trace_api_env['case'].id}/trace"
    )
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"]["error"]["code"] == "unauthenticated"


def test_unregistered_file_id_never_returns_an_arbitrary_object_url(
    client: TestClient, trace_api_env
) -> None:
    response = client.post(
        "/api/v1/trace-files/not-a-registered-file/download-url",
        json={"object_key": "attacker/chosen/object"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "trace_file_not_found"
    assert "attacker/chosen/object" not in response.text


def test_large_span_content_requires_admin_and_is_audited(
    client: TestClient, session: Session, trace_api_env
) -> None:
    child = trace_api_env["seeded"].child
    page = client.get(f"/api/v1/trace-sessions/{child.id}/spans?limit=10")
    thinking_id = page.json()["items"][0]["id"]

    response = client.post(f"/api/v1/trace-spans/{thinking_id}/content-url")

    assert response.status_code == 200, response.text
    assert "X-Amz-Expires=300" in response.json()["url"]
    assert "full private content" not in response.text
    assert "content_object_key" not in response.text
    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "trace.span.content_url")
    )
    assert audit is not None
    assert audit.target_type == "trace_span"
    assert audit.target_id == thinking_id


def test_degraded_quality_exposes_failure_and_unlinked_without_raw_content(
    client: TestClient, session: Session, trace_api_env
) -> None:
    file = trace_api_env["seeded"].file
    child = trace_api_env["seeded"].child
    file.status = "degraded"
    file.error_code = "invalid_jsonl"
    file.error_summary = "1 行损坏；Authorization: Bearer sk-secretvalue"
    file.line_no = 7
    child.status = "unlinked"
    child.parent_session_id = None
    trace_api_env["case"].trace_status = "degraded"
    session.commit()

    response = client.get(
        f"/api/v1/eval-case-runs/{trace_api_env['case'].id}/trace?attempt=1"
    )

    assert response.status_code == 200, response.text
    quality = response.json()["quality"]
    assert quality["files"]["issues"][0]["error_code"] == "invalid_jsonl"
    assert quality["files"]["issues"][0]["line_no"] == 7
    assert quality["sessions"]["unlinked_session_ids"] == [child.id]
    assert "sk-secretvalue" not in response.text


def _create_foreign_trace(
    session: Session,
    *,
    foreign_tenant: bool,
) -> tuple[EvalCaseRun, TraceSession]:
    tenant_id = str(new_id()) if foreign_tenant else str(DEFAULT_TENANT_ID)
    if foreign_tenant:
        tenant = Tenant(id=tenant_id, slug=f"foreign-{tenant_id[:8]}", name="Foreign")
        workspace = Workspace(
            id=str(new_id()),
            tenant_id=tenant_id,
            slug="default",
            name="Default",
        )
        session.add_all([tenant, workspace])
        session.flush()
    else:
        workspace = session.scalar(
            select(Workspace).where(Workspace.tenant_id == tenant_id)
        )
    project = Project(
        id=str(new_id()),
        tenant_id=tenant_id,
        workspace_id=workspace.id,
        slug="foreign",
        name="Foreign",
    )
    session.add(project)
    session.flush()
    run = EvalRun(
        tenant_id=tenant_id,
        project_id=project.id,
        name="Foreign run",
        idempotency_key=f"foreign-{new_id()}",
        created_by="test",
    )
    session.add(run)
    session.flush()
    case = EvalCaseRun(
        tenant_id=tenant_id,
        eval_run_id=run.id,
        dataset_case_id="foreign-case",
        case_key="foreign-case",
        latest_attempt_no=1,
    )
    session.add(case)
    session.flush()
    attempt = EvalCaseAttempt(
        tenant_id=tenant_id,
        eval_case_run_id=case.id,
        attempt_no=1,
        status="succeeded",
    )
    session.add(attempt)
    session.flush()
    file = TraceFile(
        tenant_id=tenant_id,
        eval_case_attempt_id=attempt.id,
        eval_case_run_id=case.id,
        runtime_key="claude-code",
        parser_version="foreign-parser",
        logical_path="foreign.jsonl",
        artifact_kind="trace",
        object_key="foreign/object",
        sha256="f" * 64,
        size=1,
        status="ready",
    )
    session.add(file)
    session.flush()
    trace_session = TraceSession(
        tenant_id=tenant_id,
        trace_file_id=file.id,
        eval_case_attempt_id=attempt.id,
        runtime_key="claude-code",
        parser_version="foreign-parser",
        native_session_id="foreign",
        native_session_key="foreign:root",
        status="ready",
    )
    session.add(trace_session)
    session.commit()
    return case, trace_session


@pytest.mark.parametrize("foreign_tenant", [False, True])
def test_cross_project_and_cross_tenant_deep_links_are_not_visible(
    client: TestClient,
    session: Session,
    trace_api_env,
    foreign_tenant: bool,
) -> None:
    case, trace_session = _create_foreign_trace(
        session, foreign_tenant=foreign_tenant
    )

    case_response = client.get(f"/api/v1/eval-case-runs/{case.id}/trace")
    session_response = client.get(
        f"/api/v1/trace-sessions/{trace_session.id}/spans"
    )

    assert case_response.status_code == 404
    assert session_response.status_code == 404
    assert case.id not in case_response.text
    assert trace_session.id not in session_response.text


def test_session_tree_query_count_does_not_scale_with_session_count(
    client: TestClient,
    engine,
    session: Session,
    trace_api_env,
) -> None:
    file = trace_api_env["seeded"].file
    for index in range(40):
        session.add(
            TraceSession(
                tenant_id=str(DEFAULT_TENANT_ID),
                trace_file_id=file.id,
                eval_case_attempt_id=trace_api_env["attempt"].id,
                runtime_key="claude-code",
                parser_version="claude-code-2.1.278-v1",
                native_session_id=f"extra-{index}",
                native_session_key=f"extra-{index}:root",
                status="ready",
                ordinal=index + 10,
            )
        )
    session.commit()
    selects = 0

    def count_selects(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get(
            f"/api/v1/eval-case-runs/{trace_api_env['case'].id}/trace?attempt=1"
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200
    assert response.json()["stats"]["session_count"] == 42
    assert selects <= 15


def test_cross_session_parent_span_is_reported_as_missing(
    client: TestClient, session: Session, trace_api_env
) -> None:
    root = trace_api_env["seeded"].root
    child = trace_api_env["seeded"].child
    root_span = session.scalar(
        select(TraceSpan).where(
            TraceSpan.session_id == root.id,
            TraceSpan.native_event_id == "root-tool",
        )
    )
    child_span = session.scalar(
        select(TraceSpan).where(
            TraceSpan.session_id == child.id,
            TraceSpan.native_event_id == "child-message",
        )
    )
    child_span.parent_span_id = root_span.id
    session.commit()

    response = client.get(f"/api/v1/trace-spans/{child_span.id}")

    assert response.status_code == 200
    assert response.json()["parent_missing"] is True


def test_negative_span_duration_marks_clock_incomparable(
    client: TestClient, session: Session, trace_api_env
) -> None:
    root = trace_api_env["seeded"].root
    session.add(
        TraceSpan(
            tenant_id=str(DEFAULT_TENANT_ID),
            session_id=root.id,
            native_event_id="clock-anomaly",
            ordinal=99,
            kind="MESSAGE",
            started_at=datetime(2026, 11, 11, 0, 0, 9, tzinfo=UTC),
            ended_at=datetime(2026, 11, 11, 0, 0, 8, tzinfo=UTC),
            attributes_json={},
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/eval-case-runs/{trace_api_env['case'].id}/trace?attempt=1"
    )

    assert response.status_code == 200
    assert response.json()["quality"]["clock_comparable"] is False

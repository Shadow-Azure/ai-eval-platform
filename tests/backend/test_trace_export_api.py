"""M8-05: attempt-level raw trace package export (admin only)."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from app.defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from app.models import (
    AuditLog,
    EvalCaseAttempt,
    Tenant,
    TraceFile,
    TraceSession,
    TraceSpan,
    User,
    Workspace,
)
from app.services import identity, trace_export
from app.services.object_store import ObjectMissing, ObjectStoreError
from conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
from fastapi.testclient import TestClient
from object_stub import StubObjectStore
from sqlalchemy import select
from sqlalchemy.orm import Session
from trace_ingest_helpers import create_env

ROOT_CONTENT = b'{"type": "root"}\n'
CHILD_CONTENT = b'{"type": "subagent"}\n'


def _login(client: TestClient, email: str, password: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text


def _add_viewer(session: Session) -> User:
    tenant = session.get(Tenant, str(DEFAULT_TENANT_ID))
    workspace = session.scalar(select(Workspace).where(Workspace.tenant_id == tenant.id))
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
class ExportSeed:
    root_file: TraceFile
    child_file: TraceFile
    root_session: TraceSession
    child_session: TraceSession


def _seed_export_trace(session: Session, env: dict) -> ExportSeed:
    attempt = env["attempt"]
    prefix = f"evaluations/{env['run'].id}/trace/{attempt.id}"
    root_key = f"{prefix}/root.jsonl"
    child_key = f"{prefix}/subagents/agent-alpha.jsonl"
    env["store"].add_bytes(root_key, ROOT_CONTENT)
    env["store"].add_bytes(child_key, CHILD_CONTENT)
    root_file = TraceFile(
        tenant_id=str(DEFAULT_TENANT_ID),
        eval_case_attempt_id=attempt.id,
        eval_case_run_id=env["case"].id,
        runtime_key="claude-code",
        runtime_version="2.1.278",
        native_format="claude-code.session+jsonl",
        format_version="1",
        parser_version="claude-code-2.1.278-v1",
        logical_path="native/projects/-testbed/root.jsonl",
        artifact_kind="trace",
        object_key=root_key,
        sha256=sha256(ROOT_CONTENT).hexdigest(),
        size=len(ROOT_CONTENT),
        detected_format="jsonl",
        status="ready",
        ordinal=0,
    )
    child_file = TraceFile(
        tenant_id=str(DEFAULT_TENANT_ID),
        eval_case_attempt_id=attempt.id,
        eval_case_run_id=env["case"].id,
        runtime_key="claude-code",
        runtime_version="2.1.278",
        native_format="claude-code.session+jsonl",
        format_version="1",
        parser_version="claude-code-2.1.278-v1",
        logical_path="native/subagents/agent-alpha.jsonl",
        artifact_kind="trace",
        object_key=child_key,
        sha256=sha256(CHILD_CONTENT).hexdigest(),
        size=len(CHILD_CONTENT),
        detected_format="jsonl",
        status="ready",
        ordinal=1,
    )
    session.add_all([root_file, child_file])
    session.flush()
    root = TraceSession(
        tenant_id=str(DEFAULT_TENANT_ID),
        trace_file_id=root_file.id,
        eval_case_attempt_id=attempt.id,
        runtime_key="claude-code",
        parser_version="claude-code-2.1.278-v1",
        native_session_id="session-root",
        native_session_key="session-root:root",
        root_session_id="session-root",
        agent_id="root",
        agent_name="main",
        status="ready",
        relation_evidence_json={"confidence": "authoritative", "type": "root"},
        metadata_json={},
        started_at=datetime(2026, 11, 11, tzinfo=UTC),
        ended_at=datetime(2026, 11, 11, 0, 0, 10, tzinfo=UTC),
        ordinal=0,
    )
    session.add(root)
    session.flush()
    child = TraceSession(
        tenant_id=str(DEFAULT_TENANT_ID),
        trace_file_id=child_file.id,
        eval_case_attempt_id=attempt.id,
        runtime_key="claude-code",
        parser_version="claude-code-2.1.278-v1",
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
    session.add_all(
        [
            TraceSpan(
                tenant_id=str(DEFAULT_TENANT_ID),
                session_id=root.id,
                native_event_id="root-message",
                ordinal=0,
                kind="MESSAGE",
                name="user",
                started_at=datetime(2026, 11, 11, tzinfo=UTC),
                attributes_json={},
                content_preview="hello",
            ),
            TraceSpan(
                tenant_id=str(DEFAULT_TENANT_ID),
                session_id=child.id,
                native_event_id="child-message",
                ordinal=0,
                kind="MESSAGE",
                name="assistant",
                started_at=datetime(2026, 11, 11, 0, 0, 3, tzinfo=UTC),
                attributes_json={},
                content_preview="review complete",
            ),
        ]
    )
    env["case"].trace_status = "ready"
    env["case"].latest_attempt_no = attempt.attempt_no
    attempt.status = "succeeded"
    session.commit()
    return ExportSeed(
        root_file=root_file,
        child_file=child_file,
        root_session=root,
        child_session=child,
    )


def _add_attempt(session: Session, env: dict, *, attempt_no: int) -> EvalCaseAttempt:
    attempt = EvalCaseAttempt(
        tenant_id=str(DEFAULT_TENANT_ID),
        eval_case_run_id=env["case"].id,
        attempt_no=attempt_no,
        status="succeeded",
    )
    session.add(attempt)
    session.commit()
    return attempt


def _export_url(case_id: str, attempt: int = 1) -> str:
    return f"/api/v1/eval-case-runs/{case_id}/trace/export?attempt={attempt}"


def _remove_object(store: StubObjectStore, object_key: str) -> None:
    path = f"/{store.bucket}/{object_key}"
    store.known.pop(path, None)
    store.bodies.pop(path, None)


@pytest.fixture
def trace_export_env(client: TestClient, session: Session, configure_settings):
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="access-id",
            S3_SECRET_KEY="must-not-leak-secret",
        )
        env = create_env(session, store, runtime_key="claude-code")
        seeded = _seed_export_trace(session, env)
        yield {**env, "seeded": seeded}


def test_admin_export_returns_zip_with_manifest_first_and_audit(
    client: TestClient, session: Session, trace_export_env
) -> None:
    case = trace_export_env["case"]
    seed = trace_export_env["seeded"]

    response = client.post(_export_url(case.id))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="trace-case-a-attempt-1.zip"')
    assert "filename*=UTF-8''trace-case-a-attempt-1.zip" in disposition
    assert int(response.headers["content-length"]) == len(response.content)

    root_name = "trace-case-a-attempt-1"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert names[0] == f"{root_name}/manifest.json"
        assert f"{root_name}/main/root.jsonl" in names
        assert f"{root_name}/subagents/reviewer/agent-alpha.jsonl" in names
        assert archive.read(f"{root_name}/main/root.jsonl") == ROOT_CONTENT
        assert (
            archive.read(f"{root_name}/subagents/reviewer/agent-alpha.jsonl")
            == CHILD_CONTENT
        )
        manifest = json.loads(archive.read(names[0]))

    assert manifest["schema_version"] == trace_export.SCHEMA_VERSION
    assert manifest["case"] == {"id": case.id, "key": "case-a"}
    assert manifest["eval_run"]["id"] == trace_export_env["run"].id
    assert manifest["attempt"]["id"] == trace_export_env["attempt"].id
    assert manifest["attempt"]["no"] == 1
    assert manifest["attempt"]["status"] == "succeeded"
    assert manifest["parser_versions"] == ["claude-code-2.1.278-v1"]
    stats = manifest["stats"]
    assert stats["session_count"] == 2
    assert stats["span_count"] == 2
    assert stats["file_count"] == 2
    assert stats["archived_file_count"] == 2
    assert stats["missing_file_count"] == 0
    assert stats["total_size"] == len(ROOT_CONTENT) + len(CHILD_CONTENT)
    assert stats["archive_size"] == len(response.content)
    assert manifest["sessions"][0]["agent_name"] == "main"
    assert manifest["sessions"][0]["trace_file_id"] == seed.root_file.id
    assert manifest["sessions"][0]["children"][0]["agent_name"] == "reviewer"
    assert manifest["sessions"][0]["children"][0]["trace_file_id"] == seed.child_file.id
    assert manifest["missing_files"] == []

    files = {item["id"]: item for item in manifest["files"]}
    assert files[seed.root_file.id]["archive_path"] == "main/root.jsonl"
    assert files[seed.root_file.id]["session_ids"] == [seed.root_session.id]
    assert files[seed.root_file.id]["sha256"] == sha256(ROOT_CONTENT).hexdigest()
    assert files[seed.root_file.id]["size"] == len(ROOT_CONTENT)
    assert files[seed.root_file.id]["missing"] is False
    assert files[seed.root_file.id]["size_mismatch"] is False
    assert files[seed.child_file.id]["archive_path"] == (
        "subagents/reviewer/agent-alpha.jsonl"
    )
    assert files[seed.child_file.id]["session_ids"] == [seed.child_session.id]

    assert b"object_key" not in response.content
    assert b"must-not-leak-secret" not in response.content
    assert seed.root_file.object_key.encode() not in response.content

    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "trace.export.download")
    )
    assert audit is not None
    assert audit.target_type == "eval_case_attempt"
    assert audit.target_id == trace_export_env["attempt"].id
    assert audit.metadata_json == {
        "result": "completed",
        "file_count": 2,
        "archived_file_count": 2,
        "missing_file_count": 0,
        "size": len(response.content),
        "attempt_no": 1,
    }


def test_subagent_file_lands_under_its_agent_directory(
    client: TestClient, session: Session, trace_export_env
) -> None:
    case = trace_export_env["case"]
    seed = trace_export_env["seeded"]

    response = client.post(_export_url(case.id))

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read(names[0]))
    assert "trace-case-a-attempt-1/subagents/reviewer/agent-alpha.jsonl" in names
    assert "trace-case-a-attempt-1/main/agent-alpha.jsonl" not in names
    subagent = next(
        item for item in manifest["files"] if item["id"] == seed.child_file.id
    )
    assert subagent["archive_path"] == "subagents/reviewer/agent-alpha.jsonl"
    assert subagent["session_ids"] == [seed.child_session.id]
    assert subagent["missing"] is False


def test_file_with_root_and_child_sessions_is_archived_once_under_main(
    client: TestClient, session: Session, trace_export_env
) -> None:
    case = trace_export_env["case"]
    seed = trace_export_env["seeded"]
    seed.child_session.trace_file_id = seed.root_file.id
    session.commit()

    response = client.post(_export_url(case.id))

    assert response.status_code == 200, response.text
    root_name = "trace-case-a-attempt-1"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read(names[0]))
    assert names.count(f"{root_name}/main/root.jsonl") == 1
    assert f"{root_name}/unlinked/agent-alpha.jsonl" in names
    root_entry = next(
        item for item in manifest["files"] if item["id"] == seed.root_file.id
    )
    assert root_entry["archive_path"] == "main/root.jsonl"
    assert set(root_entry["session_ids"]) == {
        seed.root_session.id,
        seed.child_session.id,
    }
    child_entry = next(
        item for item in manifest["files"] if item["id"] == seed.child_file.id
    )
    assert child_entry["archive_path"] == "unlinked/agent-alpha.jsonl"
    assert child_entry["session_ids"] == []


def test_object_size_mismatch_is_flagged_but_still_archived(
    client: TestClient, session: Session, trace_export_env
) -> None:
    case = trace_export_env["case"]
    seed = trace_export_env["seeded"]
    seed.child_file.size = len(CHILD_CONTENT) + 7
    session.commit()

    response = client.post(_export_url(case.id))

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read(names[0]))
    assert (
        "trace-case-a-attempt-1/subagents/reviewer/agent-alpha.jsonl" in names
    )
    files = {item["id"]: item for item in manifest["files"]}
    assert files[seed.child_file.id]["size_mismatch"] is True
    assert files[seed.root_file.id]["size_mismatch"] is False
    assert manifest["stats"]["archived_file_count"] == 2


def test_export_requires_admin_and_does_not_audit_rejected_calls(
    client: TestClient, session: Session, trace_export_env
) -> None:
    case = trace_export_env["case"]
    _add_viewer(session)
    _login(client, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD)

    forbidden = client.post(_export_url(case.id))
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["error"]["code"] == "forbidden"
    assert (
        session.scalar(
            select(AuditLog).where(AuditLog.action == "trace.export.download")
        )
        is None
    )

    client.cookies.clear()
    anonymous = client.post(_export_url(case.id))
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"]["error"]["code"] == "unauthenticated"


def test_missing_object_is_skipped_listed_and_does_not_break_the_zip(
    client: TestClient, session: Session, trace_export_env
) -> None:
    case = trace_export_env["case"]
    seed = trace_export_env["seeded"]
    _remove_object(trace_export_env["store"], seed.child_file.object_key)

    response = client.post(_export_url(case.id))

    assert response.status_code == 200, response.text
    root_name = "trace-case-a-attempt-1"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read(names[0]))
        assert archive.read(f"{root_name}/main/root.jsonl") == ROOT_CONTENT
    assert f"{root_name}/subagents/reviewer/agent-alpha.jsonl" not in names
    files = {item["id"]: item for item in manifest["files"]}
    assert files[seed.root_file.id]["missing"] is False
    assert files[seed.child_file.id]["missing"] is True
    assert manifest["missing_files"] == [
        {
            "id": seed.child_file.id,
            "logical_path": "native/subagents/agent-alpha.jsonl",
            "archive_path": "subagents/reviewer/agent-alpha.jsonl",
            "artifact_kind": "trace",
            "sha256": sha256(CHILD_CONTENT).hexdigest(),
            "size": len(CHILD_CONTENT),
            "reason": "object_missing",
        }
    ]
    stats = manifest["stats"]
    assert stats["file_count"] == 2
    assert stats["archived_file_count"] == 1
    assert stats["missing_file_count"] == 1
    assert stats["total_size"] == len(ROOT_CONTENT)

    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "trace.export.download")
    )
    assert audit is not None
    assert audit.metadata_json["result"] == "completed"
    assert audit.metadata_json["archived_file_count"] == 1
    assert audit.metadata_json["missing_file_count"] == 1


def test_attempt_without_trace_files_is_a_readable_conflict(
    client: TestClient, session: Session, trace_export_env
) -> None:
    case = trace_export_env["case"]
    _add_attempt(session, trace_export_env, attempt_no=2)

    response = client.post(_export_url(case.id, attempt=2))

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "trace_export_unavailable"
    assert (
        session.scalar(
            select(AuditLog).where(AuditLog.action == "trace.export.download")
        )
        is None
    )

    unknown = client.post(_export_url(case.id, attempt=99))
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["error"]["code"] == "attempt_not_found"


def test_archive_segments_are_sanitized_and_collisions_are_deduped() -> None:
    assert trace_export.safe_segment("../etc/passwd", fallback="file-x") == "passwd"
    assert trace_export.safe_segment("..\\..\\", fallback="file-x") == "file-x"
    assert trace_export.safe_segment("../..", fallback="file-x") == "file-x"
    assert trace_export.safe_segment("a\x00b\nc.jsonl", fallback="file-x") == "abc.jsonl"
    assert trace_export.safe_segment("中文会话.jsonl", fallback="file-x") == "jsonl"
    assert trace_export.safe_segment(None, fallback="file-x") == "file-x"

    used: set[str] = set()
    first = trace_export.unique_archive_path(
        "main/session.jsonl", used=used, seed="file-a"
    )
    second = trace_export.unique_archive_path(
        "main/session.jsonl", used=used, seed="file-b"
    )
    assert first == "main/session.jsonl"
    assert second == (
        f"main/session-{sha256(b'file-b').hexdigest()[:8]}.jsonl"
    )
    assert second not in {first}
    for path in (first, second):
        assert ".." not in path
        assert not path.startswith("/")


def test_unconfigured_object_store_returns_503(
    client: TestClient, session: Session, trace_export_env, monkeypatch
) -> None:
    case = trace_export_env["case"]
    monkeypatch.setattr(
        "app.api.traces.object_store_from_settings", lambda _settings: None
    )

    response = client.post(_export_url(case.id))

    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "object_store_unavailable"
    assert (
        session.scalar(
            select(AuditLog).where(AuditLog.action == "trace.export.download")
        )
        is None
    )


class _FailingStore:
    def head_object(self, _object_key: str) -> int:
        raise ObjectStoreError("boom")

    def iter_object(self, _object_key: str):
        raise AssertionError("iter_object must not be called")


def test_object_store_failure_returns_502_without_leaking_details(
    client: TestClient, session: Session, trace_export_env, monkeypatch
) -> None:
    case = trace_export_env["case"]
    monkeypatch.setattr(
        "app.api.traces.object_store_from_settings",
        lambda _settings: _FailingStore(),
    )

    response = client.post(_export_url(case.id))

    assert response.status_code == 502
    assert response.json()["detail"]["error"]["code"] == "trace_export_failed"
    assert "boom" not in response.text
    assert "must-not-leak-secret" not in response.text
    assert (
        session.scalar(
            select(AuditLog).where(AuditLog.action == "trace.export.download")
        )
        is None
    )


def test_unlinked_dangling_and_orphan_files_land_under_unlinked(
    client: TestClient, session: Session, trace_export_env
) -> None:
    case = trace_export_env["case"]
    seed = trace_export_env["seeded"]
    attempt = trace_export_env["attempt"]

    seed.child_session.status = "unlinked"
    session.commit()
    response = client.post(_export_url(case.id))
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read(names[0]))
    assert "trace-case-a-attempt-1/unlinked/agent-alpha.jsonl" in names
    assert not any("/subagents/reviewer/" in name for name in names)
    unlinked = next(
        item for item in manifest["files"] if item["id"] == seed.child_file.id
    )
    assert unlinked["archive_path"] == "unlinked/agent-alpha.jsonl"

    orphan_key = f"evaluations/{trace_export_env['run'].id}/trace/{attempt.id}/orphan.jsonl"
    orphan_content = b'{"type": "orphan"}\n'
    trace_export_env["store"].add_bytes(orphan_key, orphan_content)
    session.add(
        TraceFile(
            tenant_id=str(DEFAULT_TENANT_ID),
            eval_case_attempt_id=attempt.id,
            eval_case_run_id=case.id,
            runtime_key="claude-code",
            runtime_version="2.1.278",
            native_format="claude-code.session+jsonl",
            format_version="1",
            parser_version="claude-code-2.1.278-v1",
            logical_path="native/orphan.jsonl",
            artifact_kind="trace",
            object_key=orphan_key,
            sha256=sha256(orphan_content).hexdigest(),
            size=len(orphan_content),
            detected_format="jsonl",
            status="ready",
            ordinal=5,
        )
    )
    session.commit()
    response = client.post(_export_url(case.id))
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read(names[0]))
    assert "trace-case-a-attempt-1/unlinked/orphan.jsonl" in names
    orphan = next(
        item for item in manifest["files"] if item["logical_path"] == "native/orphan.jsonl"
    )
    assert orphan["session_ids"] == []


def test_midstream_missing_object_after_successful_head_is_marked(
    session: Session, trace_export_env
) -> None:
    case = trace_export_env["case"]

    class _MidstreamMissingStore:
        def head_object(self, _object_key: str) -> int:
            return 100

        def iter_object(self, object_key: str):
            yield b"partial"
            raise ObjectMissing(object_key)

    plan = trace_export.plan_trace_export(
        session,
        tenant_id=str(DEFAULT_TENANT_ID),
        project_id=str(DEFAULT_PROJECT_ID),
        case_run_id=case.id,
        attempt_no=1,
    )
    archive = trace_export.build_trace_export(plan, _MidstreamMissingStore())
    try:
        archive.file.seek(0)
        data = archive.file.read()
    finally:
        archive.close()
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        names = bundle.namelist()
        manifest = json.loads(bundle.read(names[0]))
    assert len(names) == 1
    assert manifest["stats"]["archived_file_count"] == 0
    assert manifest["stats"]["missing_file_count"] == 2


def test_unknown_case_id_returns_not_found_without_audit(
    client: TestClient, session: Session, trace_export_env
) -> None:
    response = client.post(_export_url("00000000-0000-0000-0000-000000000000"))
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "eval_case_run_not_found"
    assert (
        session.scalar(
            select(AuditLog).where(AuditLog.action == "trace.export.download")
        )
        is None
    )

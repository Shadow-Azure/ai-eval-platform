"""M6-02: native trace ingestion, isolation, degradation and redaction."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from app.models import EvalCaseAttempt, EvalCaseRun, TraceFile, TraceSession, TraceSpan
from app.services import case_attempts, trace_ingestion
from app.services.trace_parsers import (
    GenericJsonlParser,
    ParsedFile,
    ParsedSession,
    ParsedSpan,
    ParserRegistry,
)
from app.services.trace_redaction import redact_text
from case_helpers import auth, case_url, completion_body
from fastapi.testclient import TestClient
from object_stub import StubObjectStore
from sqlalchemy import select
from trace_ingest_helpers import attach_manifest, create_env, jsonl, metadata_object, trace_object

ROOT_RECORDS = [
    {
        "type": "user",
        "sessionId": "session-1",
        "uuid": "event-1",
        "timestamp": "2026-11-11T00:00:00Z",
        "message": {"role": "user", "content": "hello"},
    },
    {
        "type": "assistant",
        "sessionId": "session-1",
        "uuid": "event-2",
        "timestamp": "2026-11-11T00:00:01Z",
        "message": {"role": "assistant", "content": "world"},
        "usage": {"input_tokens": 7, "output_tokens": 3, "cost": "0.0002"},
    },
    {
        "type": "tool",
        "sessionId": "session-1",
        "uuid": "event-3",
        "timestamp": "2026-11-11T00:00:02Z",
        "message": {"role": "tool", "content": "ok"},
    },


]
class SharedSessionParser(GenericJsonlParser):
    """Two native agents that deliberately reuse the root sessionId."""

    parser_version = "shared-session-1"

    def parse(self, data, context):
        return ParsedFile(
            sessions=[
                ParsedSession(
                    native_session_id="same-session",
                    root_session_id="same-session",
                    status="ready",
                    ordinal=0,
                    spans=[
                        ParsedSpan(
                            ordinal=0,
                            kind="MESSAGE",
                            native_event_id="root-event",
                        )
                    ],
                ),
                ParsedSession(
                    native_session_id="same-session",
                    agent_id="sub-1",
                    parent_native_session_id="same-session:root",
                    root_session_id="same-session",
                    status="ready",
                    ordinal=1,
                    spans=[
                        ParsedSpan(
                            ordinal=0,
                            kind="MESSAGE",
                            native_event_id="sub-event",
                        )
                    ],
                ),
            ],
            status="ready",
            detected_format=context.detected_format,
        )


class InvalidEvidenceParser(GenericJsonlParser):
    parser_version = "invalid-evidence-1"

    def parse(self, data, context):
        return ParsedFile(
            sessions=[
                ParsedSession(
                    native_session_id="bad-confidence",
                    relation_evidence={"confidence": "guess"},
                    spans=[ParsedSpan(ordinal=0, kind="MESSAGE", native_event_id="e1")],
                ),
                ParsedSession(
                    native_session_id="derived-unversioned",
                    relation_evidence={"confidence": "derived"},
                    spans=[ParsedSpan(ordinal=0, kind="MESSAGE", native_event_id="e2")],
                ),
            ],
            status="ready",
            detected_format=context.detected_format,
        )




@pytest.fixture
def trace_env(session, configure_settings):
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        yield create_env(session, store)


def _rows(session, attempt: EvalCaseAttempt):
    files = list(
        session.scalars(
            select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
        )
    )
    sessions = list(
        session.scalars(
            select(TraceSession).where(TraceSession.eval_case_attempt_id == attempt.id)
        )
    )
    spans = list(
        session.scalars(
            select(TraceSpan).where(TraceSpan.session_id.in_([s.id for s in sessions]))
        )
    )
    return files, sessions, spans


def test_multifile_happy_path_indexes_sessions_and_spans(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    objects = [
        trace_object(
            trace_env,
            logical_path="native/projects/-testbed/session-1.jsonl",
            content=jsonl(ROOT_RECORDS),
        ),
        metadata_object(trace_env),
    ]
    attach_manifest(session, attempt, objects)

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "ready"
    assert result["files"] == 2
    assert result["ready"] == 2
    files, sessions, spans = _rows(session, attempt)
    assert {row.status for row in files} == {"ready"}
    assert {row.detected_format for row in files} == {"jsonl", "json"}
    # The subagent metadata object is provenance only; it must not synthesize
    # an execution session.
    assert len(sessions) == 1
    assert len(spans) == 3
    assert {span.kind for span in spans} == {"MESSAGE"}
    assistant = next(span for span in spans if span.native_event_id == "event-2")
    assert assistant.input_tokens == 7
    assert assistant.output_tokens == 3
    assert str(assistant.cost) == "0.000200"
    case = session.get(EvalCaseRun, trace_env["case"].id)
    assert case.trace_status == "ready"
    assert case.execution_status == "queued"
    assert case.result_status == "not_scored"


def test_size_mismatch_is_isolated_from_other_files(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    content = jsonl(ROOT_RECORDS)
    bad = trace_object(
        trace_env,
        logical_path="native/bad.jsonl",
        content=content,
        size=len(content) + 9,
    )
    good = trace_object(
        trace_env,
        logical_path="native/good.jsonl",
        content=jsonl([{"type": "message", "sessionId": "good", "uuid": "g1"}]),
    )
    attach_manifest(session, attempt, [bad, good])

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "degraded"
    files, sessions, _spans = _rows(session, attempt)
    by_path = {row.logical_path: row for row in files}
    assert by_path["native/bad.jsonl"].status == "failed"
    assert by_path["native/bad.jsonl"].error_code == "size_mismatch"
    assert by_path["native/good.jsonl"].status == "ready"
    assert {row.trace_file_id for row in sessions} == {by_path["native/good.jsonl"].id}
    case = session.get(EvalCaseRun, trace_env["case"].id)
    assert case.execution_status == "queued"
    assert case.result_status == "not_scored"


def test_digest_mismatch_is_isolated(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    bad = trace_object(
        trace_env,
        logical_path="native/bad.jsonl",
        content=jsonl(ROOT_RECORDS),
        digest="0" * 64,
    )
    good = trace_object(
        trace_env,
        logical_path="native/good.jsonl",
        content=jsonl([{"type": "message", "sessionId": "good", "uuid": "g1"}]),
    )
    attach_manifest(session, attempt, [bad, good])

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "degraded"
    files, sessions, _spans = _rows(session, attempt)
    failed = next(row for row in files if row.logical_path == "native/bad.jsonl")
    assert failed.status == "failed"
    assert failed.error_code == "digest_mismatch"
    assert all(row.trace_file_id != failed.id for row in sessions)


def test_unknown_format_degrades_with_line_number(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    obj = trace_object(
        trace_env,
        logical_path="native/opaque.bin",
        content=b'opaque-header\n{"type":"message","sessionId":"s1","uuid":"e1"}\n',
        native_format=None,
        content_type="application/octet-stream",
    )
    attach_manifest(session, attempt, [obj])

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "degraded"
    files, sessions, spans = _rows(session, attempt)
    assert files[0].status == "degraded"
    assert files[0].error_code == "invalid_jsonl"
    assert files[0].line_no == 1
    assert len(sessions) == 1
    assert len(spans) == 1


def test_corrupt_line_keeps_later_lines_and_hides_raw_content(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    secret = "sk-supersecretvalue123"
    content = (
        '{"type":"user","sessionId":"s1","uuid":"e1"}\n'
        f'{{"api_key":"{secret}", broken\n'
        '{"type":"assistant","sessionId":"s1","uuid":"e2"}\n'
    ).encode()
    obj = trace_object(trace_env, logical_path="native/partial.jsonl", content=content)
    attach_manifest(session, attempt, [obj])

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "degraded"
    files, _sessions, spans = _rows(session, attempt)
    assert files[0].status == "degraded"
    assert files[0].error_code == "invalid_jsonl"
    assert files[0].line_no == 2
    assert secret not in (files[0].error_summary or "")
    assert "api_key" not in (files[0].error_summary or "")
    assert len(spans) == 2


def test_preview_and_summary_redact_credentials(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    content = jsonl(
        [
            {
                "type": "assistant",
                "sessionId": "s1",
                "uuid": "e1",
                "api_key": "sk-verysecretvalue123",
                "headers": {"Authorization": "Bearer abcdefghijklmnop"},
            }
        ]
    )
    obj = trace_object(trace_env, logical_path="native/secret.jsonl", content=content)
    attach_manifest(session, attempt, [obj])

    trace_ingestion.ingest_attempt(session, attempt)

    _files, _sessions, spans = _rows(session, attempt)
    preview = spans[0].content_preview or ""
    assert "sk-verysecretvalue123" not in preview
    assert "abcdefghijklmnop" not in preview
    assert "[REDACTED]" in preview


def test_redact_text_masks_common_shapes() -> None:
    cases = [
        ("sk-abcdefghij", "sk-abcdefghij"),
        ("Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ("api_key=supersecret", "supersecret"),
        ("password: hunter2", "hunter2"),
        ('{"cookie": "session=abc"}', "session=abc"),
        ("https://user:pass@example.com/path", "user:pass@"),
        ("AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ]
    for raw, secret in cases:
        assert secret not in redact_text(raw)


def test_completion_enqueues_pending_without_changing_response(
    eval_env, client: TestClient, session
) -> None:
    case_run_id = eval_env["cases"]["case-a"]
    content = jsonl([{"type": "message", "sessionId": "s1", "uuid": "e1"}])
    digest = sha256(content).hexdigest()
    object_key = (
        f"evaluations/{eval_env['run_id']}/{eval_env['shard']}/attempt1/"
        f"{case_run_id}/trace/{digest}"
    )
    eval_env["store"].add_bytes(object_key, content)
    body = completion_body(eval_env, case_run_id)
    body["trace_manifest"] = {
        "manifest_version": "1.0",
        "capture_status": "complete",
        "objects": [
            {
                "artifact_kind": "trace",
                "logical_path": "native/session-1.jsonl",
                "object_key": object_key,
                "sha256": digest,
                "size": len(content),
                "content_type": "application/x-ndjson",
                "native_format": "claude-code.session+jsonl",
                "runtime_version": "2.1.278",
                "format_version": "1",
            }
        ],
    }
    response = client.post(
        case_url(eval_env, case_run_id, "completed"), json=body, headers=auth(eval_env)
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["deduped"] is False

    session.expire_all()
    attempt = session.scalar(
        select(EvalCaseAttempt).where(EvalCaseAttempt.eval_case_run_id == case_run_id)
    )
    files = list(
        session.scalars(
            select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
        )
    )
    assert len(files) == 1
    assert files[0].status == "pending"
    assert session.get(EvalCaseRun, case_run_id).trace_status == "pending"

    result = trace_ingestion.ingest_pending(session)
    assert result["ready"] == 1
    assert session.get(TraceFile, files[0].id).status == "ready"


def test_legacy_single_file_manifest_is_ingested(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    content = jsonl([{"type": "message", "sessionId": "legacy", "uuid": "l1"}])
    digest = sha256(content).hexdigest()
    object_key = f"evaluations/{trace_env['run'].id}/legacy/{digest}"
    trace_env["store"].add_bytes(object_key, content)
    attempt.artifact_manifest_json = {
        "trace_manifest": {
            "object_key": object_key,
            "sha256": digest,
            "size": len(content),
        }
    }
    session.commit()

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "ready"
    _files, sessions, spans = _rows(session, attempt)
    assert len(sessions) == 1
    assert sessions[0].native_session_id == "legacy"
    assert len(spans) == 1


def test_diagnostics_only_manifest_does_not_claim_completeness(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    diagnostic = trace_object(
        trace_env,
        logical_path="diagnostics/stdout.log",
        content=b"stdout only\n",
        kind="trace_diagnostic",
        native_format="text",
        content_type="text/plain",
    )
    attach_manifest(session, attempt, [diagnostic], capture_status="partial")

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["files"] == 0
    assert result["trace_status"] == "incomplete"
    files, sessions, _spans = _rows(session, attempt)
    assert files == []
    assert sessions == []


def test_all_files_failed_sets_trace_status_failed(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    content = jsonl([{"type": "message", "sessionId": "s1", "uuid": "e1"}])
    bad = trace_object(
        trace_env,
        logical_path="native/bad.jsonl",
        content=content,
        size=len(content) + 1,
    )
    attach_manifest(session, attempt, [bad])

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "failed"
    case = session.get(EvalCaseRun, trace_env["case"].id)
    assert case.trace_status == "failed"
    assert case.execution_status == "queued"
    assert case.result_status == "not_scored"


def test_partial_capture_with_corrupt_file_stays_incomplete(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    good = trace_object(
        trace_env,
        logical_path="native/good.jsonl",
        content=jsonl([{"type": "message", "sessionId": "good", "uuid": "g1"}]),
    )
    corrupt = trace_object(
        trace_env,
        logical_path="native/corrupt.jsonl",
        content=(
            b'{"type":"user","sessionId":"bad","uuid":"b1"}\n'
            b"not-json\n"
        ),
    )
    attach_manifest(session, attempt, [good, corrupt], capture_status="partial")

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "incomplete"
    files, _sessions, _spans = _rows(session, attempt)
    assert {row.status for row in files} == {"ready", "degraded"}
    case = session.get(EvalCaseRun, trace_env["case"].id)
    assert case.trace_status == "incomplete"


def test_root_and_subagent_can_share_native_session_id(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    obj = trace_object(
        trace_env,
        logical_path="native/projects/-testbed/root.jsonl",
        content=jsonl([{"type": "message", "sessionId": "same-session"}]),
    )
    attach_manifest(session, attempt, [obj])
    registry = ParserRegistry((SharedSessionParser(),))

    result = trace_ingestion.ingest_attempt(
        session,
        attempt,
        parser_registry=registry,
        parser_version="shared-session-1",
    )

    assert result["trace_status"] == "ready"
    _files, sessions, _spans = _rows(session, attempt)
    by_key = {row.native_session_key: row for row in sessions}
    assert set(by_key) == {"same-session:root", "same-session:sub-1"}
    assert all(row.native_session_id == "same-session" for row in sessions)
    assert by_key["same-session:sub-1"].parent_session_id == by_key["same-session:root"].id


def test_invalid_relation_evidence_degrades_file(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    obj = trace_object(
        trace_env,
        logical_path="native/relation.jsonl",
        content=jsonl([{"type": "message", "sessionId": "ignored"}]),
    )
    attach_manifest(session, attempt, [obj])
    registry = ParserRegistry((InvalidEvidenceParser(),))

    result = trace_ingestion.ingest_attempt(
        session,
        attempt,
        parser_registry=registry,
        parser_version="invalid-evidence-1",
    )

    assert result["trace_status"] == "degraded"
    files, sessions, _spans = _rows(session, attempt)
    assert files[0].status == "degraded"
    assert files[0].error_code == "invalid_relation_evidence"
    assert {row.status for row in sessions} == {"degraded"}
    assert all(
        "validation_error" in (row.relation_evidence_json or {}) for row in sessions
    )


def test_session_ordinals_are_attempt_global(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    late = trace_object(
        trace_env,
        logical_path="native/a-late.jsonl",
        content=jsonl(
            [
                {
                    "type": "message",
                    "sessionId": "late",
                    "uuid": "late-1",
                    "timestamp": "2026-11-11T00:00:10Z",
                }
            ]
        ),
    )
    early = trace_object(
        trace_env,
        logical_path="native/z-early.jsonl",
        content=jsonl(
            [
                {
                    "type": "message",
                    "sessionId": "early",
                    "uuid": "early-1",
                    "timestamp": "2026-11-11T00:00:01Z",
                }
            ]
        ),
    )
    attach_manifest(session, attempt, [late, early])

    trace_ingestion.ingest_attempt(session, attempt)

    _files, sessions, _spans = _rows(session, attempt)
    ordered = sorted(sessions, key=lambda row: row.ordinal)
    assert [row.native_session_id for row in ordered] == ["early", "late"]
    assert [row.ordinal for row in ordered] == [0, 1]


def test_generic_files_share_session_id_but_keep_distinct_keys(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    first = trace_object(
        trace_env,
        logical_path="native/projects/alpha/session.jsonl",
        content=jsonl([{"type": "message", "sessionId": "shared", "uuid": "r1"}]),
    )
    second = trace_object(
        trace_env,
        logical_path="native/projects/beta/session.jsonl",
        content=jsonl([{"type": "message", "sessionId": "shared", "uuid": "s1"}]),
    )
    attach_manifest(session, attempt, [first, second])

    trace_ingestion.ingest_attempt(session, attempt)

    _files, sessions, _spans = _rows(session, attempt)
    assert {row.native_session_id for row in sessions} == {"shared"}
    assert {row.native_session_key for row in sessions} == {
        "shared:native/projects/alpha/session",
        "shared:native/projects/beta/session",
    }


def test_artifact_trace_objects_without_manifest_are_pending_not_unlinked(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    case = session.get(EvalCaseRun, trace_env["case"].id)
    obj = trace_object(
        trace_env,
        logical_path="native/upload-only.jsonl",
        content=jsonl([{"type": "message", "sessionId": "upload", "uuid": "u1"}]),
    )
    attempt.artifact_manifest_json = {"objects": [obj]}
    session.commit()

    case_attempts.finalize_completion(
        session,
        case_run=case,
        attempt=attempt,
        execution_status="succeeded",
        result_status="resolved",
        score_manifest={},
        trace_manifest=None,
        summary={"duration_ms": 1},
        now=datetime.now(UTC),
        settings=None,
        event_type="case.reconciled_completed",
    )

    session.expire_all()
    assert session.get(EvalCaseRun, case.id).trace_status == "pending"
    row = session.scalar(
        select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
    )
    assert row is not None
    assert row.status == "pending"

"""M6-02: per-file savepoint isolation and unchanged evaluation outcomes."""

from __future__ import annotations

import pytest
from app.models import EvalCaseRun, TraceFile, TraceSession, TraceSpan
from app.services import trace_ingestion
from app.services.trace_parsers import (
    GenericJsonlParser,
    ParseError,
    ParserRegistry,
)
from object_stub import StubObjectStore
from sqlalchemy import select
from trace_ingest_helpers import attach_manifest, create_env, jsonl, trace_object


class SelectiveFailParser(GenericJsonlParser):
    parser_version = "selective-1"

    def parse(self, data, context):
        if context.logical_path and "raise" in context.logical_path:
            raise ParseError("parser_crash", "解析器在第 2 行失败", line_no=2)
        return super().parse(data, context)


@pytest.fixture
def trace_env(session, configure_settings):
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        yield create_env(session, store)


def _rows(session, attempt_id: str):
    files = list(
        session.scalars(
            select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt_id)
        )
    )
    sessions = list(
        session.scalars(
            select(TraceSession).where(TraceSession.eval_case_attempt_id == attempt_id)
        )
    )
    spans = list(
        session.scalars(
            select(TraceSpan).where(TraceSpan.session_id.in_([row.id for row in sessions]))
        )
    )
    return files, sessions, spans


def test_parser_exception_rolls_back_only_that_file(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    bad = trace_object(
        trace_env,
        logical_path="native/raise.jsonl",
        content=jsonl([{"type": "message", "sessionId": "bad", "uuid": "b1"}]),
    )
    good = trace_object(
        trace_env,
        logical_path="native/good.jsonl",
        content=jsonl([{"type": "message", "sessionId": "good", "uuid": "g1"}]),
    )
    attach_manifest(session, attempt, [bad, good])
    registry = ParserRegistry((SelectiveFailParser(),))

    result = trace_ingestion.ingest_attempt(
        session, attempt, parser_registry=registry
    )

    assert result["trace_status"] == "degraded"
    files, sessions, spans = _rows(session, attempt.id)
    by_path = {row.logical_path: row for row in files}
    assert by_path["native/raise.jsonl"].status == "failed"
    assert by_path["native/raise.jsonl"].error_code == "parser_crash"
    assert by_path["native/raise.jsonl"].line_no == 2
    assert by_path["native/good.jsonl"].status == "ready"
    assert {row.trace_file_id for row in sessions} == {by_path["native/good.jsonl"].id}
    assert len(spans) == 1
    case = session.get(EvalCaseRun, trace_env["case"].id)
    assert case.execution_status == "queued"
    assert case.result_status == "not_scored"


def test_storage_exception_mid_insert_leaves_no_partial_tree(trace_env, monkeypatch) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    bad = trace_object(
        trace_env,
        logical_path="native/bad.jsonl",
        content=jsonl([{"type": "message", "sessionId": "bad", "uuid": "b1"}]),
    )
    good = trace_object(
        trace_env,
        logical_path="native/good.jsonl",
        content=jsonl([{"type": "message", "sessionId": "good", "uuid": "g1"}]),
    )
    attach_manifest(session, attempt, [bad, good])
    original = trace_ingestion._persist_staged_tree

    def flaky_store(session, staged_files, *, attempt_id, now, session_ordinals):
        trace_file = session.get(TraceFile, staged_files[0].trace_file_id)
        if trace_file.logical_path == "native/bad.jsonl":
            session.add(
                TraceSession(
                    tenant_id=trace_file.tenant_id,
                    trace_file_id=trace_file.id,
                    eval_case_attempt_id=trace_file.eval_case_attempt_id,
                    runtime_key=trace_file.runtime_key,
                    parser_version=trace_file.parser_version,
                    native_session_id="partial-write",
                    native_session_key="partial-write:root",
                    status="ready",
                    relation_evidence_json={},
                    metadata_json={},
                    ordinal=0,
                )
            )
            session.flush()
            raise RuntimeError("boom after partial insert")
        return original(
            session,
            staged_files,
            attempt_id=attempt_id,
            now=now,
            session_ordinals=session_ordinals,
        )

    monkeypatch.setattr(trace_ingestion, "_persist_staged_tree", flaky_store)

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "degraded"
    files, sessions, _spans = _rows(session, attempt.id)
    by_path = {row.logical_path: row for row in files}
    assert by_path["native/bad.jsonl"].status == "failed"
    assert by_path["native/bad.jsonl"].error_code == "storage_error"
    assert by_path["native/good.jsonl"].status == "ready"
    assert {row.native_session_id for row in sessions} == {"good"}
    assert all(row.trace_file_id != by_path["native/bad.jsonl"].id for row in sessions)
    case = session.get(EvalCaseRun, trace_env["case"].id)
    assert case.execution_status == "queued"
    assert case.result_status == "not_scored"
    assert case.trace_status == "degraded"


def test_failed_file_can_be_repaired_after_transient_store_error(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    store = trace_env["store"]
    content = jsonl([{"type": "message", "sessionId": "repair", "uuid": "r1"}])
    obj = trace_object(
        trace_env,
        logical_path="native/repair.jsonl",
        content=content,
    )
    attach_manifest(session, attempt, [obj])
    path = f"/{store.bucket}/{obj['object_key']}"
    store.known.pop(path)
    store.bodies.pop(path)

    first = trace_ingestion.ingest_attempt(session, attempt)
    assert first["trace_status"] == "failed"
    row = session.scalar(
        select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
    )
    assert row is not None
    assert row.status == "failed"
    assert row.error_code == "artifact_missing"

    normal = trace_ingestion.ingest_attempt(session, attempt)
    assert normal["claimed"] == 0
    session.refresh(row)
    assert row.status == "failed"

    store.add_bytes(obj["object_key"], content)
    repaired = trace_ingestion.ingest_trace_file(session, row, repair=True)

    assert repaired["trace_status"] == "ready"
    session.refresh(row)
    assert row.status == "ready"
    assert row.error_code is None
    _files, sessions, spans = _rows(session, attempt.id)
    assert {session_row.native_session_id for session_row in sessions} == {"repair"}
    assert len(spans) == 1

"""M6-02: replay idempotency, parser upgrades and concurrent claims."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, TraceFile, TraceSession, TraceSpan
from app.services import case_attempts, trace_ingestion
from app.services.trace_parsers import (
    GenericJsonlParser,
    ParsedSpan,
    ParseError,
    ParserRegistry,
)
from object_stub import StubObjectStore
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from trace_ingest_helpers import attach_manifest, create_env, jsonl, trace_object


class V2Parser(GenericJsonlParser):
    parser_version = "raw-jsonl-2"

    def parse(self, data, context):
        parsed = super().parse(data, context)
        for session in parsed.sessions:
            session.spans.append(
                ParsedSpan(ordinal=len(session.spans), kind="CHAIN", name="parser-v2")
            )
        return parsed


class FailingV2Parser(V2Parser):
    """Upgrade parser that fails on the second, deliberately bad file."""

    def parse(self, data, context):
        if context.logical_path and "bad" in context.logical_path:
            raise ParseError("upgrade_failed", "v2 parser rejected the file", line_no=1)
        return super().parse(data, context)


@pytest.fixture
def trace_env(session, configure_settings):
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        yield create_env(session, store, runtime_key="generic")


def _counts(session: Session, attempt_id: str) -> tuple[int, int, int]:
    files = session.scalar(
        select(func.count()).select_from(TraceFile).where(
            TraceFile.eval_case_attempt_id == attempt_id
        )
    )
    sessions = session.scalar(
        select(func.count()).select_from(TraceSession).where(
            TraceSession.eval_case_attempt_id == attempt_id
        )
    )
    spans = session.scalar(select(func.count()).select_from(TraceSpan))
    return int(files or 0), int(sessions or 0), int(spans or 0)


def _rows(session: Session, attempt: EvalCaseAttempt):
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
            select(TraceSpan).where(TraceSpan.session_id.in_([row.id for row in sessions]))
        )
    )
    return files, sessions, spans


def test_same_digest_and_parser_replay_does_not_duplicate_rows(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    obj = trace_object(
        trace_env,
        logical_path="native/session-1.jsonl",
        content=jsonl(
            [
                {"type": "user", "sessionId": "s1", "uuid": "e1"},
                {"type": "assistant", "sessionId": "s1", "uuid": "e2"},
            ]
        ),
    )
    attach_manifest(session, attempt, [obj])

    first = trace_ingestion.ingest_attempt(session, attempt)
    assert first["trace_status"] == "ready"
    before = _counts(session, attempt.id)
    events_before = session.scalar(
        select(func.count()).select_from(EvalEvent).where(
            EvalEvent.event_type == trace_ingestion.EVENT_TYPE,
            EvalEvent.eval_case_run_id == trace_env["case"].id,
        )
    )

    second = trace_ingestion.ingest_attempt(session, attempt)

    assert second["skipped"] == 1
    assert second["ready"] == 0
    assert _counts(session, attempt.id) == before
    events_after = session.scalar(
        select(func.count()).select_from(EvalEvent).where(
            EvalEvent.event_type == trace_ingestion.EVENT_TYPE,
            EvalEvent.eval_case_run_id == trace_env["case"].id,
        )
    )
    assert events_after == events_before


def test_parser_upgrade_replaces_tree_and_keeps_provenance(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    obj = trace_object(
        trace_env,
        logical_path="native/session-1.jsonl",
        content=jsonl([{"type": "message", "sessionId": "s1", "uuid": "e1"}]),
    )
    attach_manifest(session, attempt, [obj])
    trace_ingestion.ingest_attempt(session, attempt)
    assert _counts(session, attempt.id) == (1, 1, 1)

    registry = ParserRegistry((GenericJsonlParser(), V2Parser()))
    upgraded = trace_ingestion.ingest_attempt(
        session,
        attempt,
        parser_registry=registry,
        parser_version="raw-jsonl-2",
    )

    assert upgraded["replaced"] is True
    assert upgraded["trace_status"] == "ready"
    files = list(
        session.scalars(
            select(TraceFile)
            .where(TraceFile.eval_case_attempt_id == attempt.id)
            .order_by(TraceFile.parser_version)
        )
    )
    assert [(row.parser_version, row.status) for row in files] == [
        ("raw-jsonl-1", "ready"),
        ("raw-jsonl-2", "ready"),
    ]
    sessions = list(
        session.scalars(
            select(TraceSession).where(TraceSession.eval_case_attempt_id == attempt.id)
        )
    )
    assert {row.parser_version for row in sessions} == {"raw-jsonl-2"}
    spans = list(
        session.scalars(
            select(TraceSpan).where(TraceSpan.session_id.in_([row.id for row in sessions]))
        )
    )
    assert len(spans) == 2
    assert {span.kind for span in spans} == {"MESSAGE", "CHAIN"}
    case = session.get(EvalCaseRun, trace_env["case"].id)
    assert case.trace_status == "ready"


def test_concurrent_duplicate_ingestion_keeps_one_tree(trace_env, engine) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    obj = trace_object(
        trace_env,
        logical_path="native/session-1.jsonl",
        content=jsonl([{"type": "message", "sessionId": "s1", "uuid": "e1"}]),
    )
    attach_manifest(session, attempt, [obj])
    attempt_id = attempt.id
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker() -> None:
        barrier.wait(timeout=5)
        # SQLite serializes writers; a real worker retries its next sweep.
        for _ in range(10):
            try:
                with Session(engine) as thread_session:
                    thread_attempt = thread_session.get(EvalCaseAttempt, attempt_id)
                    trace_ingestion.ingest_attempt(thread_session, thread_attempt)
                return
            except OperationalError:
                time.sleep(0.05)
        try:
            raise AssertionError("trace ingestion kept hitting a database lock")
        except AssertionError as exc:  # surfaced by the outer assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    files, sessions, spans = _counts(session, attempt_id)
    assert files == 1
    assert sessions == 1
    assert spans == 1
    events = session.scalar(
        select(func.count()).select_from(EvalEvent).where(
            EvalEvent.event_type == trace_ingestion.EVENT_TYPE,
            EvalEvent.eval_case_run_id == trace_env["case"].id,
        )
    )
    assert events == 1


def test_parser_upgrade_failure_keeps_previous_tree(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    good = trace_object(
        trace_env,
        logical_path="native/a-good.jsonl",
        content=jsonl([{"type": "message", "sessionId": "good", "uuid": "g1"}]),
    )
    bad = trace_object(
        trace_env,
        logical_path="native/b-bad.jsonl",
        content=jsonl([{"type": "message", "sessionId": "bad", "uuid": "b1"}]),
    )
    attach_manifest(session, attempt, [good, bad])
    first = trace_ingestion.ingest_attempt(session, attempt)
    assert first["trace_status"] == "ready"
    _files, old_sessions, old_spans = _rows(session, attempt)
    old_session_ids = {row.id for row in old_sessions}
    old_span_ids = {row.id for row in old_spans}
    assert len(old_session_ids) == 2

    registry = ParserRegistry((GenericJsonlParser(), FailingV2Parser()))
    upgraded = trace_ingestion.ingest_attempt(
        session,
        attempt,
        parser_registry=registry,
        parser_version="raw-jsonl-2",
    )

    assert upgraded["replaced"] is False
    assert upgraded["upgrade_aborted"] is True
    files, sessions, spans = _rows(session, attempt)
    assert {row.id for row in sessions} == old_session_ids
    assert {row.id for row in spans} == old_span_ids
    by_version = {row.parser_version: row for row in files}
    assert by_version["raw-jsonl-1"].status == "ready"
    v2 = [row for row in files if row.parser_version == "raw-jsonl-2"]
    assert {row.status for row in v2} == {"failed", "degraded"}
    failed = next(row for row in v2 if row.status == "failed")
    assert failed.error_code == "upgrade_failed"
    aborted = next(row for row in v2 if row.status == "degraded")
    assert aborted.error_code == "parser_upgrade_aborted"


def test_stale_attempt_ingestion_does_not_override_case_status(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    case = session.get(EvalCaseRun, trace_env["case"].id)
    stale = EvalCaseAttempt(
        tenant_id=attempt.tenant_id,
        eval_case_run_id=case.id,
        attempt_no=2,
        status="succeeded",
        shard_id=attempt.shard_id,
    )
    session.add(stale)
    case.latest_attempt_no = 2
    case.trace_status = "incomplete"
    session.commit()
    obj = trace_object(
        trace_env,
        logical_path="native/stale.jsonl",
        content=jsonl([{"type": "message", "sessionId": "stale", "uuid": "s1"}]),
    )
    attach_manifest(session, attempt, [obj])

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["trace_status"] == "ready"
    files, sessions, spans = _rows(session, attempt)
    assert len(files) == 1
    assert len(sessions) == 1
    assert len(spans) == 1
    session.expire_all()
    assert session.get(EvalCaseRun, case.id).trace_status == "incomplete"
    event = session.scalar(
        select(EvalEvent).where(
            EvalEvent.eval_case_run_id == case.id,
            EvalEvent.event_type == trace_ingestion.EVENT_TYPE,
        )
    )
    assert event is not None
    assert event.attempt_no == attempt.attempt_no


def test_single_file_v2_ingest_cannot_leave_partial_tree(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    good = trace_object(
        trace_env,
        logical_path="native/a-good.jsonl",
        content=jsonl([{"type": "message", "sessionId": "good", "uuid": "g1"}]),
    )
    bad = trace_object(
        trace_env,
        logical_path="native/b-bad.jsonl",
        content=jsonl([{"type": "message", "sessionId": "bad", "uuid": "b1"}]),
    )
    attach_manifest(session, attempt, [good, bad])
    trace_ingestion.ingest_attempt(session, attempt)
    _files, old_sessions, old_spans = _rows(session, attempt)
    old_session_ids = {row.id for row in old_sessions}
    old_span_ids = {row.id for row in old_spans}

    v2_row = TraceFile(
        tenant_id=attempt.tenant_id,
        eval_case_attempt_id=attempt.id,
        eval_case_run_id=attempt.eval_case_run_id,
        runtime_key="claude-code",
        parser_version="raw-jsonl-2",
        logical_path=good["logical_path"],
        artifact_kind=good["artifact_kind"],
        object_key=good["object_key"],
        sha256=good["sha256"],
        size=good["size"],
        detected_format="jsonl",
        status="pending",
        ordinal=0,
    )
    session.add(v2_row)
    session.commit()

    registry = ParserRegistry((GenericJsonlParser(), FailingV2Parser()))
    result = trace_ingestion.ingest_trace_file(
        session, v2_row, parser_registry=registry
    )

    assert result["upgrade_aborted"] is True
    files, sessions, spans = _rows(session, attempt)
    assert {row.id for row in sessions} == old_session_ids
    assert {row.id for row in spans} == old_span_ids
    assert {row.parser_version for row in sessions} == {"raw-jsonl-1"}
    v2_files = [row for row in files if row.parser_version == "raw-jsonl-2"]
    assert {row.status for row in v2_files} == {"failed", "degraded"}


def test_stale_attempt_finalize_completion_keeps_case_trace_status(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    case = session.get(EvalCaseRun, trace_env["case"].id)
    session.add(
        EvalCaseAttempt(
            tenant_id=attempt.tenant_id,
            eval_case_run_id=case.id,
            attempt_no=2,
            status="running",
            shard_id=attempt.shard_id,
        )
    )
    case.latest_attempt_no = 2
    case.trace_status = "incomplete"
    obj = trace_object(
        trace_env,
        logical_path="native/stale-finalize.jsonl",
        content=jsonl([{"type": "message", "sessionId": "stale", "uuid": "s1"}]),
    )
    attach_manifest(session, attempt, [obj])
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
    assert session.get(EvalCaseRun, case.id).trace_status == "incomplete"


def test_stale_publication_uses_atomic_latest_attempt_guard(trace_env, engine) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    case = session.get(EvalCaseRun, trace_env["case"].id)
    obj = trace_object(
        trace_env,
        logical_path="native/stale-publish.jsonl",
        content=jsonl([{"type": "message", "sessionId": "stale", "uuid": "s1"}]),
    )
    attach_manifest(session, attempt, [obj])
    trace_ingestion.enqueue_attempt(session, attempt)
    row = session.scalar(
        select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
    )
    assert row is not None
    row.status = "ready"
    case.trace_status = "pending"
    session.commit()
    with Session(engine) as snapshot:
        stale_case = snapshot.get(EvalCaseRun, case.id)
        assert stale_case.latest_attempt_no == 1

    session.add(
        EvalCaseAttempt(
            tenant_id=attempt.tenant_id,
            eval_case_run_id=case.id,
            attempt_no=2,
            status="running",
            shard_id=attempt.shard_id,
        )
    )
    case.latest_attempt_no = 2
    case.trace_status = "incomplete"
    session.commit()

    trace_ingestion._publish_trace_state(
        session,
        attempt=attempt,
        case_run=stale_case,
        run=trace_env["run"],
        expected=1,
        parser_versions={row.parser_version},
    )

    session.expire_all()
    fresh = session.get(EvalCaseRun, case.id)
    assert fresh.latest_attempt_no == 2
    assert fresh.trace_status == "incomplete"
    event = session.scalar(
        select(EvalEvent).where(
            EvalEvent.eval_case_run_id == case.id,
            EvalEvent.event_type == trace_ingestion.EVENT_TYPE,
            EvalEvent.attempt_no == attempt.attempt_no,
        )
    )
    assert event is not None


def test_mixed_parser_version_batch_cannot_bypass_atomic_replace(
    trace_env, monkeypatch
) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    first = trace_object(
        trace_env,
        logical_path="native/a-good.jsonl",
        content=jsonl([{"type": "message", "sessionId": "a", "uuid": "a1"}]),
    )
    second = trace_object(
        trace_env,
        logical_path="native/b-bad.jsonl",
        content=jsonl([{"type": "message", "sessionId": "b", "uuid": "b1"}]),
    )
    attach_manifest(session, attempt, [first, second])
    trace_ingestion.ingest_attempt(session, attempt)
    _files, old_sessions, _spans = _rows(session, attempt)
    old_session_ids = {row.id for row in old_sessions}

    def mixed_resolver(registry, *, run, manifest, item, parser_version):
        detected = trace_ingestion.detect_format(
            logical_path=item.get("logical_path"),
            content_type=item.get("content_type"),
            native_format=item.get("native_format"),
        )
        if "b-bad" in (item.get("logical_path") or ""):
            return detected, FailingV2Parser()
        return detected, GenericJsonlParser()

    monkeypatch.setattr(trace_ingestion, "_resolve_parser", mixed_resolver)

    result = trace_ingestion.ingest_attempt(session, attempt)

    assert result["upgrade_aborted"] is True
    assert result["replaced"] is False
    files, sessions, _spans = _rows(session, attempt)
    assert {row.id for row in sessions} == old_session_ids
    assert {row.parser_version for row in sessions} == {"raw-jsonl-1"}
    assert any(
        row.parser_version == "raw-jsonl-2" and row.status == "failed"
        for row in files
    )

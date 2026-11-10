"""M6-02: deduped trace.ingestion.updated events with counts and redaction."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, TraceFile
from app.services import evaluations, trace_ingestion
from object_stub import StubObjectStore
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from trace_ingest_helpers import attach_manifest, create_env, jsonl, trace_object


@pytest.fixture
def trace_env(session, configure_settings):
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        yield create_env(session, store)


def _events(session, case_run_id: str) -> list[EvalEvent]:
    return list(
        session.scalars(
            select(EvalEvent)
            .where(
                EvalEvent.eval_case_run_id == case_run_id,
                EvalEvent.event_type == trace_ingestion.EVENT_TYPE,
            )
            .order_by(EvalEvent.sequence)
        )
    )


def test_ingestion_event_is_deduped_and_reports_redacted_counts(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    secret = "sk-eventsecretvalue123"
    partial = trace_object(
        trace_env,
        logical_path="native/partial.jsonl",
        content=(
            '{"type":"user","sessionId":"s1","uuid":"e1"}\n'
            f'{{"api_key":"{secret}", broken\n'
            '{"type":"assistant","sessionId":"s1","uuid":"e2"}\n'
        ).encode(),
    )
    attach_manifest(session, attempt, [partial])

    trace_ingestion.ingest_attempt(session, attempt)
    events = _events(session, trace_env["case"].id)
    assert len(events) == 1
    payload = events[0].payload_json
    assert payload["trace_status"] == "degraded"
    assert payload["attempt_id"] == attempt.id
    assert payload["file_counts"] == {
        "expected": 1,
        "pending": 0,
        "parsing": 0,
        "ready": 0,
        "degraded": 1,
        "failed": 0,
    }
    assert payload["parser_versions"] == ["raw-jsonl-1"]
    assert payload["files"][0]["line_no"] == 2
    assert "行不是有效 JSON" in payload["error_summary"]
    assert secret not in payload["error_summary"]
    assert secret not in str(payload["files"])

    trace_ingestion.ingest_attempt(session, attempt)
    assert len(_events(session, trace_env["case"].id)) == 1

    good = trace_object(
        trace_env,
        logical_path="native/good.jsonl",
        content=jsonl([{"type": "message", "sessionId": "s2", "uuid": "g1"}]),
    )
    attach_manifest(session, attempt, [partial, good])
    trace_ingestion.ingest_attempt(session, attempt)
    events = _events(session, trace_env["case"].id)
    assert len(events) == 2
    assert events[-1].payload_json["file_counts"] == {
        "expected": 2,
        "pending": 0,
        "parsing": 0,
        "ready": 1,
        "degraded": 1,
        "failed": 0,
    }
    assert events[-1].payload_json["fingerprint"] != events[0].payload_json["fingerprint"]


def test_concurrent_file_events_are_deduped(trace_env, engine) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    first = trace_object(
        trace_env,
        logical_path="native/first.jsonl",
        content=jsonl([{"type": "message", "sessionId": "first", "uuid": "f1"}]),
    )
    second = trace_object(
        trace_env,
        logical_path="native/second.jsonl",
        content=jsonl([{"type": "message", "sessionId": "second", "uuid": "s1"}]),
    )
    attach_manifest(session, attempt, [first, second])
    trace_ingestion.enqueue_attempt(session, attempt)
    session.commit()
    row_ids = [
        row.id
        for row in session.scalars(
            select(TraceFile)
            .where(TraceFile.eval_case_attempt_id == attempt.id)
            .order_by(TraceFile.logical_path)
        )
    ]
    assert len(row_ids) == 2
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(row_id: str) -> None:
        barrier.wait(timeout=5)
        for _ in range(20):
            try:
                with Session(engine) as thread_session:
                    row = thread_session.get(TraceFile, row_id)
                    trace_ingestion.ingest_trace_file(thread_session, row)
                return
            except OperationalError:
                time.sleep(0.05)
        errors.append(AssertionError("concurrent trace ingestion stayed locked"))

    threads = [threading.Thread(target=worker, args=(row_id,)) for row_id in row_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    events = _events(session, trace_env["case"].id)
    final = [
        event
        for event in events
        if event.payload_json["file_counts"]["ready"] == 2
    ]
    assert len(final) == 1
    fingerprints = [event.payload_json["fingerprint"] for event in events]
    assert len(fingerprints) == len(set(fingerprints))
    assert events[-1].payload_json["trace_status"] == "ready"


def test_concurrent_publication_never_regresses_sequence(trace_env, engine) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    first = trace_object(
        trace_env,
        logical_path="native/first.jsonl",
        content=jsonl([{"type": "message", "sessionId": "first", "uuid": "f1"}]),
    )
    second = trace_object(
        trace_env,
        logical_path="native/second.jsonl",
        content=jsonl([{"type": "message", "sessionId": "second", "uuid": "s1"}]),
    )
    attach_manifest(session, attempt, [first, second])
    trace_ingestion.enqueue_attempt(session, attempt)
    session.commit()
    row_ids = [
        row.id
        for row in session.scalars(
            select(TraceFile)
            .where(TraceFile.eval_case_attempt_id == attempt.id)
            .order_by(TraceFile.logical_path)
        )
    ]
    assert len(row_ids) == 2

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(row_id: str) -> None:
        barrier.wait(timeout=5)
        for _ in range(20):
            try:
                with Session(engine) as thread_session:
                    row = thread_session.get(TraceFile, row_id)
                    trace_ingestion.ingest_trace_file(thread_session, row)
                return
            except OperationalError:
                time.sleep(0.05)
        errors.append(AssertionError("concurrent publication stayed locked"))

    threads = [threading.Thread(target=worker, args=(row_id,)) for row_id in row_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    events = _events(session, trace_env["case"].id)
    sequences = [event.sequence for event in events]
    assert sequences == sorted(sequences)
    statuses = [event.payload_json["trace_status"] for event in events]
    assert statuses.count("ready") == 1
    assert events[-1].payload_json["trace_status"] == "ready"
    seen_ready = False
    for status in statuses:
        if seen_ready:
            assert status == "ready"
        seen_ready = seen_ready or status == "ready"


def test_publish_stale_cases_settles_terminal_tree(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    case = trace_env["case"]
    first = trace_object(
        trace_env,
        logical_path="native/first.jsonl",
        content=jsonl([{"type": "message", "sessionId": "first", "uuid": "f1"}]),
    )
    second = trace_object(
        trace_env,
        logical_path="native/second.jsonl",
        content=jsonl([{"type": "message", "sessionId": "second", "uuid": "s1"}]),
    )
    attach_manifest(session, attempt, [first, second])
    trace_ingestion.enqueue_attempt(session, attempt)
    rows = list(
        session.scalars(
            select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
        )
    )
    assert len(rows) == 2
    for row in rows:
        row.status = "ready"
    case.trace_status = "pending"
    session.commit()

    published = trace_ingestion.publish_stale_cases(session, limit=10)

    session.expire_all()
    assert published == {"scanned": 1, "published": 1, "events": 1}
    assert session.get(type(case), case.id).trace_status == "ready"
    events = _events(session, case.id)
    assert len(events) == 1
    assert events[0].payload_json["trace_status"] == "ready"

    again = trace_ingestion.publish_stale_cases(session, limit=10)
    assert again == {"scanned": 0, "published": 0, "events": 0}
    assert len(_events(session, case.id)) == 1


def test_publish_stale_cases_scans_past_inflight_backlog(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    case = trace_env["case"]
    first = trace_object(
        trace_env,
        logical_path="native/first.jsonl",
        content=jsonl([{"type": "message", "sessionId": "first", "uuid": "f1"}]),
    )
    attach_manifest(session, attempt, [first])
    trace_ingestion.enqueue_attempt(session, attempt)
    row = session.scalar(
        select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
    )
    assert row is not None
    row.status = "ready"

    base = datetime.now(UTC)
    backlog_ids: list[str] = []
    for index in range(11):
        run, _ = evaluations.create_synthetic_evaluation(
            session,
            name=f"inflight backlog {index}",
            case_keys=[f"backlog-{index}"],
            idempotency_key=f"stale-sweep-backlog-{index}",
            created_by="agent",
        )
        backlog_case = session.scalar(
            select(EvalCaseRun).where(EvalCaseRun.eval_run_id == run.id)
        )
        assert backlog_case is not None
        backlog_case.trace_status = "pending"
        backlog_case.created_at = base - timedelta(minutes=60 - index)
        backlog_ids.append(backlog_case.id)
        backlog_attempt = session.scalar(
            select(EvalCaseAttempt).where(
                EvalCaseAttempt.eval_case_run_id == backlog_case.id
            )
        )
        assert backlog_attempt is not None
        session.add(
            TraceFile(
                tenant_id=backlog_attempt.tenant_id,
                eval_case_attempt_id=backlog_attempt.id,
                eval_case_run_id=backlog_case.id,
                parser_version="raw-jsonl-1",
                logical_path=f"native/inflight-{index}.jsonl",
                artifact_kind="trace",
                object_key=f"backlog/{index}.jsonl",
                sha256=f"{index:064d}",
                size=1,
                status="pending",
            )
        )
    case.trace_status = "pending"
    case.created_at = base + timedelta(minutes=1)
    session.commit()

    published = trace_ingestion.publish_stale_cases(session, limit=1)

    session.expire_all()
    assert published == {"scanned": 1, "published": 1, "events": 1}
    assert session.get(type(case), case.id).trace_status == "ready"
    assert len(_events(session, case.id)) == 1
    for backlog_id in backlog_ids:
        assert session.get(EvalCaseRun, backlog_id).trace_status == "pending"
        assert _events(session, backlog_id) == []


def test_publish_stale_cases_does_not_scan_terminal_incomplete(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    case = trace_env["case"]
    obj = trace_object(
        trace_env,
        logical_path="native/incomplete.jsonl",
        content=jsonl([{"type": "message", "sessionId": "inc", "uuid": "i1"}]),
    )
    attach_manifest(session, attempt, [obj], capture_status="partial")
    trace_ingestion.enqueue_attempt(session, attempt)
    row = session.scalar(
        select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
    )
    assert row is not None
    row.status = "ready"
    case.trace_status = "incomplete"
    session.commit()

    published = trace_ingestion.publish_stale_cases(session, limit=10)

    session.expire_all()
    assert published == {"scanned": 0, "published": 0, "events": 0}
    assert session.get(type(case), case.id).trace_status == "incomplete"
    assert _events(session, case.id) == []

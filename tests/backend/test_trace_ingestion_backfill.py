"""M6-02: completed pre-ingestion attempts are backfilled into trace_file."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.models import EvalCaseAttempt, TraceFile
from app.services import trace_ingestion
from object_stub import StubObjectStore
from sqlalchemy import select
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


def test_backfill_enqueues_completed_attempt_without_trace_rows(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    attempt.status = "succeeded"
    obj = trace_object(
        trace_env,
        logical_path="native/backfill.jsonl",
        content=jsonl([{"type": "message", "sessionId": "old", "uuid": "e1"}]),
    )
    attach_manifest(session, attempt, [obj])
    assert (
        session.scalar(
            select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
        )
        is None
    )

    backfilled = trace_ingestion.backfill_missing(session, limit=10)

    assert backfilled == {"attempts": 1, "files": 1}
    row = session.scalar(
        select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
    )
    assert row is not None
    assert row.status == "pending"

    ingested = trace_ingestion.ingest_pending(session)
    assert ingested["ready"] == 1
    session.refresh(row)
    assert row.status == "ready"


def test_backfill_limit_does_not_starve_manifest_attempts(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    case = trace_env["case"]
    now = datetime.now(UTC)
    for offset in range(3):
        session.add(
            EvalCaseAttempt(
                tenant_id=attempt.tenant_id,
                eval_case_run_id=case.id,
                attempt_no=10 + offset,
                status="succeeded",
                shard_id=attempt.shard_id,
                created_at=now + timedelta(seconds=offset),
            )
        )
    attempt.status = "succeeded"
    attempt.created_at = now + timedelta(seconds=10)
    obj = trace_object(
        trace_env,
        logical_path="native/late-backfill.jsonl",
        content=jsonl([{"type": "message", "sessionId": "late", "uuid": "e1"}]),
    )
    attach_manifest(session, attempt, [obj])
    session.commit()

    backfilled = trace_ingestion.backfill_missing(session, limit=2)

    assert backfilled == {"attempts": 1, "files": 1}
    row = session.scalar(
        select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
    )
    assert row is not None
    assert row.status == "pending"


def test_backfill_limit_resumes_without_skipping_eligible_attempts(trace_env) -> None:
    session = trace_env["session"]
    first = trace_env["attempt"]
    case = trace_env["case"]
    now = datetime.now(UTC)
    attempts = [first]
    for offset in range(14):
        attempt = EvalCaseAttempt(
            tenant_id=first.tenant_id,
            eval_case_run_id=case.id,
            attempt_no=20 + offset,
            status="succeeded",
            shard_id=first.shard_id,
            created_at=now + timedelta(seconds=offset),
        )
        session.add(attempt)
        attempts.append(attempt)
    first.status = "succeeded"
    first.created_at = now + timedelta(seconds=20)
    for index, attempt in enumerate(attempts):
        obj = trace_object(
            trace_env,
            logical_path=f"native/backfill-{index}.jsonl",
            content=jsonl(
                [{"type": "message", "sessionId": f"backfill-{index}", "uuid": f"e{index}"}]
            ),
        )
        attach_manifest(session, attempt, [obj])

    assert trace_ingestion.backfill_missing(session, limit=10) == {
        "attempts": 10,
        "files": 10,
    }
    assert trace_ingestion.backfill_missing(session, limit=10) == {
        "attempts": 5,
        "files": 5,
    }
    assert trace_ingestion.backfill_missing(session, limit=10) == {
        "attempts": 0,
        "files": 0,
    }


def test_backfill_keyset_skips_diagnostics_beyond_old_window(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    case = trace_env["case"]
    now = datetime.now(UTC)
    diagnostic = trace_object(
        trace_env,
        logical_path="diagnostics/stdout.log",
        content=b"stdout only\n",
        kind="trace_diagnostic",
        native_format="text",
        content_type="text/plain",
    )
    for offset in range(45):
        noisy = EvalCaseAttempt(
            tenant_id=attempt.tenant_id,
            eval_case_run_id=case.id,
            attempt_no=100 + offset,
            status="succeeded",
            shard_id=attempt.shard_id,
            created_at=now + timedelta(seconds=offset),
        )
        noisy.artifact_manifest_json = {"objects": [diagnostic]}
        session.add(noisy)
    attempt.status = "succeeded"
    attempt.created_at = now + timedelta(seconds=100)
    obj = trace_object(
        trace_env,
        logical_path="native/real-backfill.jsonl",
        content=jsonl([{"type": "message", "sessionId": "real", "uuid": "e1"}]),
    )
    attach_manifest(session, attempt, [obj])
    session.commit()

    backfilled = trace_ingestion.backfill_missing(session, limit=10)

    assert backfilled == {"attempts": 1, "files": 1}
    row = session.scalar(
        select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
    )
    assert row is not None
    assert row.status == "pending"

"""M6-02: file leases protect concurrent workers and expire safely."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.models import TraceFile
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
        yield create_env(session, store, runtime_key="generic")


def _pending_row(trace_env) -> TraceFile:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    obj = trace_object(
        trace_env,
        logical_path="native/lease.jsonl",
        content=jsonl([{"type": "message", "sessionId": "lease", "uuid": "e1"}]),
    )
    attach_manifest(session, attempt, [obj])
    trace_ingestion.enqueue_attempt(session, attempt)
    session.commit()
    row = session.scalar(
        select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)
    )
    assert row is not None
    return row


def test_fresh_lease_blocks_second_worker(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    now = datetime.now(UTC)
    row = _pending_row(trace_env)
    row.status = "parsing"
    row.lease_until = now + timedelta(seconds=120)
    session.commit()

    result = trace_ingestion.ingest_attempt(session, attempt, now=now)

    assert result["skipped"] == 1
    assert result["claimed"] == 0
    session.refresh(row)
    assert row.status == "parsing"


def test_expired_lease_can_be_reclaimed(trace_env) -> None:
    session = trace_env["session"]
    attempt = trace_env["attempt"]
    now = datetime.now(UTC)
    row = _pending_row(trace_env)
    row.status = "parsing"
    row.lease_until = now - timedelta(seconds=1)
    session.commit()

    result = trace_ingestion.ingest_attempt(session, attempt, now=now)

    assert result["claimed"] == 1
    assert result["trace_status"] == "ready"
    session.refresh(row)
    assert row.status == "ready"


def test_reclaimed_file_cannot_be_renewed_or_finished_by_old_owner(trace_env) -> None:
    session = trace_env["session"]
    now = datetime.now(UTC)
    row = _pending_row(trace_env)

    assert trace_ingestion._claim(
        session, row.id, now=now, lease_seconds=60, owner="worker-a"
    )
    session.refresh(row)
    assert row.lease_owner == "worker-a"

    row.lease_until = now - timedelta(seconds=1)
    session.commit()
    assert trace_ingestion._claim(
        session, row.id, now=now, lease_seconds=60, owner="worker-b"
    )
    session.refresh(row)
    assert row.lease_owner == "worker-b"

    assert not trace_ingestion._renew_lease(
        session, row.id, now=now, lease_seconds=60, owner="worker-a"
    )
    assert not trace_ingestion._finish_lease(
        session,
        row.id,
        owner="worker-a",
        status="ready",
        error_code=None,
        error_summary=None,
        line_no=None,
        now=now,
    )
    session.refresh(row)
    assert row.status == "parsing"
    assert row.lease_owner == "worker-b"

    assert trace_ingestion._renew_lease(
        session, row.id, now=now, lease_seconds=60, owner="worker-b"
    )
    assert trace_ingestion._finish_lease(
        session,
        row.id,
        owner="worker-b",
        status="ready",
        error_code=None,
        error_summary=None,
        line_no=None,
        now=now,
    )
    session.refresh(row)
    assert row.status == "ready"
    assert row.lease_owner is None

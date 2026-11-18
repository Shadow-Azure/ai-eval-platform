"""M8-43 (#304): job timeout > case timeout > Agent timeout.

The execute job used to be a static 60 minutes while a run could freeze a
60-minute Agent budget. These tests pin the derived hierarchy, the dispatch
payload the workflow reads, and the partial-trace path used by a cancelled
runner.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.agent_timeout import (
    CASE_TIMEOUT_HEADROOM_SECONDS,
    JOB_TIMEOUT_HEADROOM_SECONDS,
    TimeoutHierarchyError,
    dispatch_timeout_seconds,
    effective_case_timeout,
    job_timeout_minutes,
    job_timeout_seconds,
    minimum_case_timeout,
)
from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalRun, Outbox, TraceFile
from app.seed import ensure_default_scope
from app.services import case_attempts, evaluations
from case_helpers import failure_body
from sqlalchemy.orm import Session

SETTINGS = get_settings()


def _create_run(
    session: Session,
    *,
    key: str,
    agent_timeout_seconds: int = 1800,
    case_timeout_seconds: int | None = None,
    max_attempts: int = 2,
) -> EvalRun:
    ensure_default_scope(session)
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="超时层级",
        case_keys=["case-a"],
        idempotency_key=key,
        created_by="agent",
        case_timeout_seconds=case_timeout_seconds,
        agent_timeout_seconds=agent_timeout_seconds,
        max_attempts=max_attempts,
        settings=SETTINGS,
    )
    return run


def _case_attempt(session: Session, run: EvalRun) -> tuple[EvalCaseRun, EvalCaseAttempt]:
    case = (
        session.query(EvalCaseRun)
        .filter(EvalCaseRun.eval_run_id == run.id)
        .one()
    )
    attempt = (
        session.query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case.id)
        .one()
    )
    return case, attempt


def _dispatch(session: Session, run: EvalRun) -> tuple[dict, dict]:
    row = (
        session.query(Outbox)
        .filter(Outbox.aggregate_id == run.id)
        .one()
    )
    return row.payload_json["client_payload"], json.loads(row.payload_json["manifest_text"])


def test_agent_case_and_job_budgets_are_strictly_ordered() -> None:
    case = effective_case_timeout(
        agent_timeout_seconds=3600,
        requested_case_timeout_seconds=4200,
        max_case_timeout_seconds=SETTINGS.eval_max_case_timeout_seconds,
    )
    assert case == 3600 + CASE_TIMEOUT_HEADROOM_SECONDS == 4800
    job = job_timeout_seconds(case)
    assert job == case + JOB_TIMEOUT_HEADROOM_SECONDS == 5400
    assert dispatch_timeout_seconds(case) > job > case > 3600
    assert job_timeout_minutes(case) == 90
    assert minimum_case_timeout(3600) == 4800


def test_equal_case_and_agent_budget_is_raised_to_the_minimum() -> None:
    case = effective_case_timeout(
        agent_timeout_seconds=3600,
        requested_case_timeout_seconds=3600,
        max_case_timeout_seconds=SETTINGS.eval_max_case_timeout_seconds,
    )
    assert case == 4800


def test_agent_budget_too_large_for_the_case_cap_is_rejected() -> None:
    with pytest.raises(TimeoutHierarchyError):
        effective_case_timeout(
            agent_timeout_seconds=86400,
            requested_case_timeout_seconds=3600,
            max_case_timeout_seconds=SETTINGS.eval_max_case_timeout_seconds,
        )


def test_run_creation_freezes_an_adjusted_case_budget(session: Session) -> None:
    run = _create_run(
        session,
        key="timeout-raise",
        agent_timeout_seconds=3600,
        case_timeout_seconds=4200,
    )
    assert run.agent_timeout_seconds == 3600
    assert run.case_timeout_seconds == 4800


def test_run_creation_rejects_an_agent_budget_without_headroom(session: Session) -> None:
    ensure_default_scope(session)
    with pytest.raises(evaluations.EvaluationInvalid) as excinfo:
        evaluations.create_synthetic_evaluation(
            session,
            name="超时层级",
            case_keys=["case-too-large"],
            idempotency_key="timeout-too-large",
            created_by="agent",
            agent_timeout_seconds=86400,
            settings=SETTINGS,
        )
    assert excinfo.value.field == "agent_timeout_seconds"


def test_dispatch_payload_carries_the_derived_job_timeout_and_window(
    session: Session,
) -> None:
    run = _create_run(
        session,
        key="timeout-dispatch",
        agent_timeout_seconds=3600,
        case_timeout_seconds=4200,
    )
    payload, manifest = _dispatch(session, run)
    assert payload["job_timeout_minutes"] == job_timeout_minutes(4800) == 90
    assert manifest["case_timeout_seconds"] == 4800
    assert manifest["agent_timeout_seconds"] == 3600

    now = datetime.now(UTC)
    deadline = datetime.fromisoformat(manifest["deadline"].replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    assert deadline > now + timedelta(seconds=4800)
    assert expires_at >= deadline


def test_job_timeout_follows_the_agent_budget(session: Session) -> None:
    short = _create_run(session, key="timeout-short", agent_timeout_seconds=1800)
    long = _create_run(
        session,
        key="timeout-long",
        agent_timeout_seconds=3600,
        case_timeout_seconds=4200,
    )
    short_payload, _ = _dispatch(session, short)
    long_payload, _ = _dispatch(session, long)
    assert short_payload["job_timeout_minutes"] == 70  # case 3600 + 600 = 70m
    assert long_payload["job_timeout_minutes"] == 90
    assert long_payload["job_timeout_minutes"] > short_payload["job_timeout_minutes"]


def _partial_trace(run: EvalRun, case: EvalCaseRun, attempt: EvalCaseAttempt) -> dict:
    return {
        "manifest_version": "1.0",
        "capture_status": "partial",
        "incomplete_reasons": ["runner_cancelled"],
        "runtime_version": "2.1.278",
        "format_version": "1",
        "objects": [
            {
                "artifact_kind": "trace",
                "logical_path": "claude/stream.jsonl",
                "object_key": (
                    f"evaluations/{run.id}/{attempt.shard_id}/attempt{attempt.attempt_no}"
                    f"/{case.id}/trace/{'a' * 64}"
                ),
                "sha256": "a" * 64,
                "size": 128,
                "content_type": "application/x-ndjson",
                "native_format": "claude-code+jsonl",
                "runtime_version": "2.1.278",
                "format_version": "1",
            }
        ],
    }


def test_runner_lost_failure_with_partial_trace_is_terminal_and_ingestible(
    session: Session,
) -> None:
    run = _create_run(session, key="timeout-partial", max_attempts=2)
    case, attempt = _case_attempt(session, run)
    payload = {
        **failure_body(case.id, error_code="runner_lost", retryable=False),
        "trace_manifest": _partial_trace(run, case, attempt),
    }

    result = case_attempts.handle_failed(session, payload=payload)

    assert result["retry_queued"] is False
    attempts = (
        session.query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case.id)
        .all()
    )
    assert len(attempts) == 1, "a cancelled job must not trigger a full re-run"
    assert attempts[0].status == "failed"
    assert attempts[0].error_code == "runner_lost"
    session.refresh(case)
    assert case.execution_status == "failed"
    assert case.error_code == "runner_lost"
    # A partial capture is explicitly incomplete, never advertised as ready.
    assert case.trace_status == "incomplete"
    trace_rows = (
        session.query(TraceFile)
        .filter(TraceFile.eval_case_attempt_id == attempts[0].id)
        .all()
    )
    assert len(trace_rows) == 1
    assert trace_rows[0].status == "pending"


def test_terminal_failure_without_trace_is_marked_unlinked(session: Session) -> None:
    run = _create_run(
        session, key="timeout-no-trace", max_attempts=1
    )
    case, _ = _case_attempt(session, run)
    case_attempts.handle_failed(
        session,
        payload=failure_body(case.id, error_code="execution_timeout", retryable=False),
    )
    session.refresh(case)
    assert case.execution_status == "timeout"
    assert case.trace_status == "unlinked"

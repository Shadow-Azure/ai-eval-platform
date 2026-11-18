"""M9 (#263) terminal bad-case enqueue and the worker reporting pass.

Covers the bad-case rule (non-resolved + actually executed), payload
freezing/determinism, idempotent re-trigger, the HE response mapping
(201/200/409/401/429...), the retry ladder and the attempts cap.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.config import get_settings
from app.models import (
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    SelfEvolutionReport,
    TestCaseResult,
    TraceFile,
    TraceSession,
)
from app.secrets import get_secret_box
from app.services import evaluations as evaluation_service
from app.services import self_evolution as swe
from sqlalchemy import select
from sqlalchemy.orm import Session

NOW = datetime(2026, 11, 17, 0, 0, 0, tzinfo=UTC)
SIGNING_KEY = "test-signing-key"
PUBLIC_BASE = "https://eval.example.com"


def aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive timestamps; normalize like the rest of the suite."""

    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _seed_enabled_run(session: Session, *, case_keys: list[str]) -> EvalRun:
    run, _ = evaluation_service.create_synthetic_evaluation(
        session,
        name="self-evolution-report",
        case_keys=case_keys,
        idempotency_key=f"swe-{hashlib.sha1('&'.join(case_keys).encode()).hexdigest()[:12]}",
        created_by="agent",
        runtime_key="claude-code",
        self_evolution_enabled=True,
        self_evolution_base_url="https://he.example.com",
        self_evolution_workspace_id="ws-prod-01",
        self_evolution_token_encrypted="cipher",
        settings=get_settings(),
        now=NOW,
    )
    return run


def _cases_by_key(session: Session, run: EvalRun) -> dict[str, EvalCaseRun]:
    return {
        row.case_key: row
        for row in session.scalars(
            select(EvalCaseRun).where(EvalCaseRun.eval_run_id == run.id)
        )
    }


def _reports_for(session: Session, run: EvalRun) -> list[SelfEvolutionReport]:
    return list(
        session.scalars(
            select(SelfEvolutionReport).where(SelfEvolutionReport.eval_run_id == run.id)
        )
    )


def _finish_case(
    session: Session,
    case: EvalCaseRun,
    *,
    execution_status: str,
    result_status: str,
    started: bool = True,
    ended_at: datetime | None = NOW,
    error_summary: str | None = None,
) -> EvalCaseAttempt:
    case.execution_status = execution_status
    case.result_status = result_status
    case.error_summary = error_summary
    attempt = (
        session.query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case.id)
        .one()
    )
    if started:
        attempt.started_at = NOW - timedelta(minutes=10)
    attempt.ended_at = ended_at
    attempt.status = execution_status
    return attempt


def _seed_trace(
    session: Session,
    attempt: EvalCaseAttempt,
    case: EvalCaseRun,
    native_id: str = "11111111-2222-3333-4444-555555555555",
) -> str:
    trace_file = TraceFile(
        tenant_id=attempt.tenant_id,
        eval_case_attempt_id=attempt.id,
        eval_case_run_id=case.id,
        runtime_key="claude-code",
        parser_version="claude-code-2.1.278-v1",
        artifact_kind="trace",
        object_key=f"traces/{attempt.id}.jsonl",
        sha256="0" * 64,
        size=12,
        status="ready",
    )
    session.add(trace_file)
    session.flush()
    session.add(
        TraceSession(
            tenant_id=attempt.tenant_id,
            trace_file_id=trace_file.id,
            eval_case_attempt_id=attempt.id,
            runtime_key="claude-code",
            parser_version="claude-code-2.1.278-v1",
            native_session_id=native_id,
            native_session_key=native_id,
            status="ready",
        )
    )
    session.flush()
    return native_id


def test_terminal_run_enqueues_only_executed_bad_cases(
    session: Session, configure_settings
) -> None:
    configure_settings(
        SELF_EVO_SESSION_SIGNING_KEY=SIGNING_KEY,
        SELF_EVO_PUBLIC_BASE_URL=PUBLIC_BASE,
    )
    run = _seed_enabled_run(session, case_keys=["case-good", "case-bad", "case-queue-cancel"])
    cases = _cases_by_key(session, run)
    _finish_case(
        session,
        cases["case-good"],
        execution_status="succeeded",
        result_status="resolved",
    )
    bad_attempt = _finish_case(
        session,
        cases["case-bad"],
        execution_status="failed",
        result_status="unresolved",
        error_summary="runner_failed: exit 1",
    )
    _finish_case(
        session,
        cases["case-queue-cancel"],
        execution_status="cancelled",
        result_status="not_scored",
        started=False,
        ended_at=None,
    )
    session.add(
        TestCaseResult(
            tenant_id=run.tenant_id,
            eval_case_attempt_id=bad_attempt.id,
            test_name="tests::test_f2p_one",
            test_group="F2P",
            outcome="failed",
        )
    )
    session.add(
        TestCaseResult(
            tenant_id=run.tenant_id,
            eval_case_attempt_id=bad_attempt.id,
            test_name="tests::test_p2p_one",
            test_group="P2P",
            outcome="failed",
        )
    )
    native_id = _seed_trace(session, bad_attempt, cases["case-bad"])

    run.status = "running"
    evaluation_service.refresh_run_progress(session, run, now=NOW)
    session.commit()

    assert run.status == "partial"
    reports = _reports_for(session, run)
    assert len(reports) == 1
    row = reports[0]
    assert row.status == "pending"
    assert row.eval_case_run_id == cases["case-bad"].id

    payload = row.payload_json
    expected_bad_case_id = (
        "swe-"
        + hashlib.sha1(f"{run.tenant_id}:{run.id}:case-bad".encode()).hexdigest()[:20]
    )
    assert payload["badCaseId"] == expected_bad_case_id
    assert row.bad_case_id == expected_bad_case_id
    assert payload["sessionId"] == native_id
    assert payload["badCaseType"] == "discovery"
    assert payload["objectType"] == "agent"
    assert payload["objectName"] == "claude-code"
    assert payload["title"] == "[SWE-bench] case-bad · unresolved"
    assert "runner_failed: exit 1" in payload["reason"]
    assert "tests::test_f2p_one" in payload["reason"]
    assert "tests::test_p2p_one" in payload["reason"]
    assert payload["source"] == "ai-eval-platform"
    assert payload["createdAt"] == NOW.isoformat(timespec="microseconds")
    meta = payload["metaData"]
    assert meta["instanceId"] == "case-bad"
    assert meta["evalRunId"] == run.id
    assert meta["evalCaseRunId"] == cases["case-bad"].id
    assert meta["attemptId"] == bad_attempt.id
    assert meta["resolved"] is False
    assert meta["failToPassFailed"] == ["tests::test_f2p_one"]
    assert meta["passToPassRegressed"] == ["tests::test_p2p_one"]

    session_source = payload["sessionSource"]
    assert session_source["runtime"] == "claude-code"
    template = session_source["sessionUrlTemplate"]
    assert template.startswith(f"{PUBLIC_BASE}/api/v1/self-evolution/sessions/")
    assert template.count("{sessionId}") == 1
    # The endpoint prefix + run scope + exp verify against the same HMAC: HE
    # substitutes the root and every child session id into the template, so
    # the grant is not bound to one concrete id — only to this eval run.
    assert f"run={run.id}" in template
    exp = int(template.split("exp=")[1].split("&")[0])
    sig = template.split("sig=")[1]
    assert swe.session_signature_matches(SIGNING_KEY, run.id, exp, sig)
    other_run_id = "0f0e0d0c-0b0a-0908-0706-050403020100"
    assert not swe.session_signature_matches(SIGNING_KEY, other_run_id, exp, sig)
    assert exp == int((NOW + timedelta(seconds=swe.SESSION_TTL_SECONDS)).timestamp())

    events = list(
        session.scalars(
            select(EvalEvent).where(
                EvalEvent.eval_run_id == run.id,
                EvalEvent.event_type == "eval.self_evolution.enqueued",
            )
        )
    )
    assert len(events) == 1
    assert events[0].payload_json["count"] == 1


def test_enqueue_is_idempotent_and_disabled_runs_are_skipped(session: Session) -> None:
    run = _seed_enabled_run(session, case_keys=["case-bad"])
    cases = _cases_by_key(session, run)
    _finish_case(session, cases["case-bad"], execution_status="failed", result_status="unresolved")

    run.status = "running"
    evaluation_service.refresh_run_progress(session, run, now=NOW)
    session.commit()
    assert len(_reports_for(session, run)) == 1

    # Re-running the terminal transition (e.g. reconciler replay) must not
    # duplicate rows.
    assert swe.enqueue_self_evolution_reports(session, run, now=NOW) == 0
    assert swe.enqueue_self_evolution_reports(session, run, now=NOW) == 0
    session.commit()
    assert len(_reports_for(session, run)) == 1

    disabled_run, _ = evaluation_service.create_synthetic_evaluation(
        session,
        name="disabled",
        case_keys=["case-x"],
        idempotency_key="swe-disabled",
        created_by="agent",
        settings=get_settings(),
        now=NOW,
    )
    assert swe.enqueue_self_evolution_reports(session, disabled_run, now=NOW) == 0


def test_not_scored_with_execution_is_reported_with_reason(session: Session) -> None:
    run = _seed_enabled_run(session, case_keys=["case-exec-fail"])
    cases = _cases_by_key(session, run)
    _finish_case(
        session,
        cases["case-exec-fail"],
        execution_status="failed",
        result_status="not_scored",
    )
    run.status = "running"
    evaluation_service.refresh_run_progress(session, run, now=NOW)
    session.commit()
    reports = _reports_for(session, run)
    assert len(reports) == 1
    assert "执行失败" in reports[0].payload_json["reason"]


def test_cancelling_run_does_not_enqueue(session: Session) -> None:
    run = _seed_enabled_run(session, case_keys=["case-bad"])
    cases = _cases_by_key(session, run)
    _finish_case(session, cases["case-bad"], execution_status="failed", result_status="unresolved")
    run.status = "cancelling"
    evaluation_service.refresh_run_progress(session, run, now=NOW)
    session.commit()
    assert run.status == "cancelled"
    assert _reports_for(session, run) == []


def test_session_id_falls_back_to_attempt_uuid(session: Session) -> None:
    run = _seed_enabled_run(session, case_keys=["case-no-trace"])
    cases = _cases_by_key(session, run)
    attempt = _finish_case(
        session, cases["case-no-trace"], execution_status="failed", result_status="unresolved"
    )
    run.status = "running"
    evaluation_service.refresh_run_progress(session, run, now=NOW)
    session.commit()
    reports = _reports_for(session, run)
    assert len(reports) == 1
    assert reports[0].payload_json["sessionId"] == attempt.id


def test_long_native_session_id_falls_back_without_session_source(
    session: Session, configure_settings
) -> None:
    configure_settings(
        SELF_EVO_SESSION_SIGNING_KEY=SIGNING_KEY,
        SELF_EVO_PUBLIC_BASE_URL=PUBLIC_BASE,
    )
    run = _seed_enabled_run(session, case_keys=["case-long-session"])
    case = _cases_by_key(session, run)["case-long-session"]
    attempt = _finish_case(
        session, case, execution_status="failed", result_status="unresolved"
    )
    _seed_trace(session, attempt, case, native_id="a" * 40)
    run.status = "running"
    evaluation_service.refresh_run_progress(session, run, now=NOW)
    session.commit()

    reports = _reports_for(session, run)
    assert len(reports) == 1
    payload = reports[0].payload_json
    assert payload["sessionId"] == attempt.id
    assert "sessionSource" not in payload


def test_backfill_rebuilds_succeeded_reports_with_session_source(
    session: Session, configure_settings
) -> None:
    configure_settings(SELF_EVO_SESSION_SIGNING_KEY="", SELF_EVO_PUBLIC_BASE_URL="")
    run = _seed_enabled_run(session, case_keys=["case-bad", "case-no-trace"])
    cases = _cases_by_key(session, run)
    bad_attempt = _finish_case(
        session,
        cases["case-bad"],
        execution_status="failed",
        result_status="unresolved",
        error_summary="runner_failed: exit 1",
    )
    _finish_case(
        session,
        cases["case-no-trace"],
        execution_status="failed",
        result_status="unresolved",
    )
    native_id = _seed_trace(session, bad_attempt, cases["case-bad"])
    run.status = "running"
    evaluation_service.refresh_run_progress(session, run, now=NOW)
    session.commit()

    reports = _reports_for(session, run)
    assert len(reports) == 2
    by_case = {report.eval_case_run_id: report for report in reports}
    for report in reports:
        assert "sessionSource" not in report.payload_json
        report.status = "succeeded"
        report.reported_at = NOW
        report.last_status_code = 201
    session.commit()

    # Without configuration, the original succeeded rows stay untouched.
    result = swe.backfill_missing_session_sources(session, run)
    assert result == {
        "scanned": 2,
        "updated": 0,
        "skipped_existing": 0,
        "skipped_no_trace": 1,
        "skipped_not_configured": 1,
    }
    assert by_case[cases["case-bad"].id].status == "succeeded"

    configure_settings(
        SELF_EVO_SESSION_SIGNING_KEY=SIGNING_KEY,
        SELF_EVO_PUBLIC_BASE_URL=PUBLIC_BASE,
    )
    dry = swe.backfill_missing_session_sources(session, run, dry_run=True)
    assert dry["updated"] == 1
    assert by_case[cases["case-bad"].id].status == "succeeded"

    result = swe.backfill_missing_session_sources(
        session, run, now=NOW + timedelta(minutes=5)
    )
    assert result["updated"] == 1
    report = by_case[cases["case-bad"].id]
    assert report.status == "pending"
    assert report.attempts == 0
    assert report.last_status_code is None
    assert report.reported_at is None
    assert report.payload_json["sessionId"] == native_id
    source = report.payload_json["sessionSource"]
    assert source["runtime"] == "claude-code"
    assert f"{PUBLIC_BASE}/api/v1/self-evolution/sessions/" in source["sessionUrlTemplate"]
    assert source["sessionUrlTemplate"].count("{sessionId}") == 1
    assert f"run={run.id}" in source["sessionUrlTemplate"]

    # The no-trace row cannot produce an envelope and stays untouched.
    no_trace = by_case[cases["case-no-trace"].id]
    assert no_trace.status == "succeeded"
    assert "sessionSource" not in no_trace.payload_json

    events = list(
        session.scalars(
            select(EvalEvent).where(
                EvalEvent.eval_run_id == run.id,
                EvalEvent.event_type == "eval.self_evolution.backfilled",
            )
        )
    )
    assert len(events) == 1
    assert events[0].payload_json["count"] == 1


# ---------------------------------------------------------------------------
# Worker pass
# ---------------------------------------------------------------------------


class FakeTransport(httpx.BaseTransport):
    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _seed_report(session: Session, run: EvalRun, case: EvalCaseRun) -> SelfEvolutionReport:
    row = SelfEvolutionReport(
        tenant_id=run.tenant_id,
        eval_run_id=run.id,
        eval_case_run_id=case.id,
        bad_case_id=f"swe-{hashlib.sha1(case.id.encode()).hexdigest()[:20]}",
        status="pending",
        payload_json={
            "badCaseId": "swe-x",
            "title": "t",
            "reason": "r",
            "sessionId": "11111111-2222-3333-4444-555555555555",
        },
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def enabled_run_with_report(session: Session) -> tuple[EvalRun, SelfEvolutionReport]:
    box = get_secret_box()
    cipher, _ = box.encrypt("whk_worker_token")
    run, _ = evaluation_service.create_synthetic_evaluation(
        session,
        name="worker-report",
        case_keys=["case-w"],
        idempotency_key="swe-worker",
        created_by="agent",
        self_evolution_enabled=True,
        self_evolution_base_url="https://he.example.com",
        self_evolution_workspace_id="ws-prod-01",
        self_evolution_token_encrypted=cipher,
        settings=get_settings(),
        now=NOW,
    )
    cases = _cases_by_key(session, run)
    row = _seed_report(session, run, cases["case-w"])
    return run, row


def test_worker_reports_success_201_and_200(
    session: Session, enabled_run_with_report
) -> None:
    _run, row = enabled_run_with_report
    statuses = iter([201, 200])
    transport = FakeTransport(lambda request: httpx.Response(next(statuses), json={"id": 1}))

    outcome = swe.process_pending_self_evolution_reports(session, transport=transport, now=NOW)
    assert outcome["succeeded"] == 1
    session.refresh(row)
    assert row.status == "succeeded"
    assert aware(row.reported_at) == NOW
    assert row.last_status_code == 201

    row.status = "pending"  # replay path: HE 200-overwrite also settles the row
    row.reported_at = None
    session.commit()
    outcome = swe.process_pending_self_evolution_reports(session, transport=transport, now=NOW)
    assert outcome["succeeded"] == 1
    session.refresh(row)
    assert row.status == "succeeded"
    assert row.last_status_code == 200

    request = transport.requests[0]
    assert str(request.url) == "https://he.example.com/api/workspaces/ws-prod-01/bad-cases"
    assert request.headers["Authorization"] == "Bearer whk_worker_token"
    assert json.loads(request.content)["badCaseId"] == "swe-x"


def test_worker_maps_terminal_client_errors(session: Session, enabled_run_with_report) -> None:
    _run, row = enabled_run_with_report
    for status, fragment in (
        (401, "凭据"),
        (403, "凭据"),
        (409, "配额"),
        (422, "契约"),
    ):
        row.status = "pending"
        row.attempts = 0
        row.next_attempt_at = None
        row.last_error = None
        session.commit()
        transport = FakeTransport(
            lambda request, status=status: httpx.Response(status, text="boom")
        )
        outcome = swe.process_pending_self_evolution_reports(session, transport=transport, now=NOW)
        assert outcome["failed"] == 1, status
        session.refresh(row)
        assert row.status == "failed", status
        assert row.last_status_code == status
        assert fragment in (row.last_error or ""), status


def test_worker_retries_429_with_ladder_then_succeeds(
    session: Session, enabled_run_with_report
) -> None:
    _run, row = enabled_run_with_report
    transport = FakeTransport(lambda request: httpx.Response(429, text="slow down"))

    outcome = swe.process_pending_self_evolution_reports(session, transport=transport, now=NOW)
    assert outcome == {"scanned": 1, "succeeded": 0, "failed": 0, "retried": 1}
    session.refresh(row)
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.last_status_code == 429
    assert aware(row.next_attempt_at) == NOW + timedelta(seconds=60)

    # Not due yet.
    early = swe.process_pending_self_evolution_reports(
        session, transport=transport, now=NOW + timedelta(seconds=30)
    )
    assert early["scanned"] == 0

    # Due again; the ladder advances to 5 minutes.
    outcome = swe.process_pending_self_evolution_reports(
        session, transport=transport, now=NOW + timedelta(seconds=61)
    )
    assert outcome["retried"] == 1
    session.refresh(row)
    assert row.attempts == 2
    assert aware(row.next_attempt_at) == NOW + timedelta(seconds=61 + 300)

    # Success on the third attempt settles the row.
    ok_transport = FakeTransport(lambda request: httpx.Response(201, json={"id": 9}))
    outcome = swe.process_pending_self_evolution_reports(
        session, transport=ok_transport, now=NOW + timedelta(seconds=400)
    )
    assert outcome["succeeded"] == 1
    session.refresh(row)
    assert row.status == "succeeded"


def test_worker_gives_up_after_attempt_cap(session: Session, enabled_run_with_report) -> None:
    _run, row = enabled_run_with_report
    transport = FakeTransport(lambda request: httpx.Response(503, text="down"))
    tick = NOW
    for _index in range(swe.MAX_REPORT_ATTEMPTS):
        outcome = swe.process_pending_self_evolution_reports(
            session, transport=transport, now=tick
        )
        assert outcome["scanned"] == 1
        tick += timedelta(hours=2)
    session.refresh(row)
    assert row.status == "failed"
    assert row.attempts == swe.MAX_REPORT_ATTEMPTS
    assert row.next_attempt_at is None


def test_worker_treats_network_error_as_retryable(
    session: Session, enabled_run_with_report
) -> None:
    _run, row = enabled_run_with_report

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    transport = FakeTransport(explode)
    outcome = swe.process_pending_self_evolution_reports(session, transport=transport, now=NOW)
    assert outcome["retried"] == 1
    session.refresh(row)
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.last_status_code is None
    assert "网络错误" in (row.last_error or "")


def test_worker_fails_when_token_undecryptable(session: Session) -> None:
    run, _ = evaluation_service.create_synthetic_evaluation(
        session,
        name="bad-cipher",
        case_keys=["case-c"],
        idempotency_key="swe-bad-cipher",
        created_by="agent",
        self_evolution_enabled=True,
        self_evolution_base_url="https://he.example.com",
        self_evolution_workspace_id="ws-prod-01",
        self_evolution_token_encrypted="not-a-fernet-token",
        settings=get_settings(),
        now=NOW,
    )
    cases = _cases_by_key(session, run)
    row = _seed_report(session, run, cases["case-c"])
    outcome = swe.process_pending_self_evolution_reports(session, now=NOW)
    assert outcome["failed"] == 1
    session.refresh(row)
    assert row.status == "failed"
    assert "解密" in (row.last_error or "")

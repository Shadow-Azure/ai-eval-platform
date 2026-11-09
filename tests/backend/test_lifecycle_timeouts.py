"""M4-02 A1: five timeout classes, phase tracking and timeout settlement.

每一类超时都必须有稳定错误码、终态与可诊断事件；边界由注入的 ``now``
决定。phase_changed_at 是队列/评分/上传宽限期的唯一计时起点（updated_at
会因任何写入而刷新，不能当阶段时钟）。验收还要求：终态超时不得把用例留在
queued/running/scoring/uploading。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox, Runner
from app.seed import ensure_default_scope
from app.services import case_attempts, evaluations, lifecycle, reaper, runners
from case_helpers import auth, case_url, started_body, upload_body
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

TIMEOUT_CODES = (
    lifecycle.QUEUE_TIMEOUT,
    lifecycle.EXECUTION_TIMEOUT,
    lifecycle.SCORING_TIMEOUT,
    lifecycle.UPLOAD_TIMEOUT,
    lifecycle.HEARTBEAT_TIMEOUT,
)
ZERO_COUNTS = {code: 0 for code in TIMEOUT_CODES}


@pytest.fixture
def lifecycle_settings(configure_settings):
    """Small timeouts so the tests can cross each boundary with ``now``."""

    configure_settings(
        EVAL_QUEUE_TIMEOUT_SECONDS=60,
        EVAL_EXECUTION_TIMEOUT_SECONDS=900,
        EVAL_SCORING_TIMEOUT_SECONDS=90,
        EVAL_UPLOAD_GRACE_SECONDS=75,
        EVAL_HEARTBEAT_TIMEOUT_SECONDS=30,
        EVAL_LIFECYCLE_BATCH_SIZE=10,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(
    session: Session,
    *,
    key: str,
    case_keys: tuple[str, ...] = ("case-a",),
    case_timeout_seconds: int = 600,
    max_attempts: int = 2,
) -> EvalRun:
    ensure_default_scope(session)
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="超时边界",
        case_keys=list(case_keys),
        idempotency_key=key,
        created_by="agent",
        settings=get_settings(),
    )
    run.case_timeout_seconds = case_timeout_seconds
    run.max_attempts = max_attempts
    session.commit()
    return run


def _case_and_attempt(
    session: Session, run: EvalRun, case_key: str = "case-a", attempt_no: int = 1
) -> tuple[EvalCaseRun, EvalCaseAttempt]:
    case = (
        session.query(EvalCaseRun)
        .filter(EvalCaseRun.eval_run_id == run.id, EvalCaseRun.case_key == case_key)
        .one()
    )
    attempt = (
        session.query(EvalCaseAttempt)
        .filter(
            EvalCaseAttempt.eval_case_run_id == case.id,
            EvalCaseAttempt.attempt_no == attempt_no,
        )
        .one()
    )
    return case, attempt


def _sweep(session: Session, now: datetime) -> dict[str, int]:
    return reaper.sweep_lifecycle(session, settings=get_settings(), now=now)


def _events(session: Session, case_run_id: str, attempt_no: int = 1) -> list[EvalEvent]:
    return (
        session.query(EvalEvent)
        .filter(
            EvalEvent.eval_case_run_id == case_run_id,
            EvalEvent.attempt_no == attempt_no,
        )
        .order_by(EvalEvent.sequence)
        .all()
    )


def _case_events(session: Session, case_run_id: str, attempt_no: int = 1) -> list[EvalEvent]:
    return [
        event
        for event in _events(session, case_run_id, attempt_no)
        if event.event_type.startswith("case.")
    ]


def _active_attempts(session: Session, run: EvalRun) -> list[EvalCaseAttempt]:
    return (
        session.query(EvalCaseAttempt)
        .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
        .filter(
            EvalCaseRun.eval_run_id == run.id,
            EvalCaseAttempt.status.in_(case_attempts.ACTIVE_ATTEMPT_STATUSES),
        )
        .all()
    )


def _reload(session: Session, row):
    session.expire_all()
    return session.get(type(row), row.id)


def _assert_terminal_timeout(
    session: Session,
    run: EvalRun,
    case: EvalCaseRun,
    attempt: EvalCaseAttempt,
    code: str,
    *,
    case_status: str,
) -> None:
    attempt = _reload(session, attempt)
    case = _reload(session, case)
    assert attempt.status == "timeout"
    assert attempt.error_code == code
    assert attempt.ended_at is not None
    assert attempt.phase_changed_at is not None
    assert case.execution_status == case_status
    assert case.error_code == code
    # 终态超时绝不能把任何 attempt 留在活跃状态。
    assert _active_attempts(session, run) == []
    events = _case_events(session, case.id)
    assert [event.event_type for event in events] == ["case.timeout"]
    payload = events[0].payload_json
    assert payload["timeout_class"] == code
    assert payload["error_code"] == code
    assert payload["attempt_id"] == attempt.id


# ---------------------------------------------------------------------------
# Five timeout classes (each with the just-before boundary)
# ---------------------------------------------------------------------------


def test_queue_timeout_uses_phase_changed_at(session: Session, lifecycle_settings) -> None:
    now = datetime.now(UTC)
    run = _make_run(session, key="queue-timeout")
    case, attempt = _case_and_attempt(session, run)
    attempt.status = "queued"
    attempt.phase_changed_at = now - timedelta(seconds=59)
    session.commit()

    # Just inside the 60s queue budget: nothing happens.
    assert _sweep(session, now) == ZERO_COUNTS
    assert _reload(session, attempt).status == "queued"
    assert _case_events(session, case.id) == []

    # One second past the budget fires, even though updated_at is fresh.
    attempt = _reload(session, attempt)
    attempt.phase_changed_at = now - timedelta(seconds=61)
    session.commit()
    counts = _sweep(session, now)
    assert counts[lifecycle.QUEUE_TIMEOUT] == 1
    assert sum(counts.values()) == 1
    _assert_terminal_timeout(
        session, run, case, attempt, lifecycle.QUEUE_TIMEOUT, case_status="timeout"
    )


def test_execution_timeout_uses_started_at_before_heartbeat(session: Session, lifecycle_settings) -> None:
    now = datetime.now(UTC)
    run = _make_run(session, key="execution-timeout", case_timeout_seconds=120)
    case, attempt = _case_and_attempt(session, run)
    attempt.status = "running"
    attempt.started_at = now - timedelta(seconds=119)
    attempt.heartbeat_at = now  # fresh heartbeat must not mask the agent timeout
    session.commit()

    assert _sweep(session, now) == ZERO_COUNTS
    assert _reload(session, attempt).status == "running"

    attempt = _reload(session, attempt)
    attempt.started_at = now - timedelta(seconds=121)
    session.commit()
    counts = _sweep(session, now)
    assert counts[lifecycle.EXECUTION_TIMEOUT] == 1
    assert counts[lifecycle.HEARTBEAT_TIMEOUT] == 0
    _assert_terminal_timeout(
        session, run, case, attempt, lifecycle.EXECUTION_TIMEOUT, case_status="timeout"
    )


def test_scoring_timeout_uses_phase_changed_at(session: Session, lifecycle_settings) -> None:
    now = datetime.now(UTC)
    run = _make_run(session, key="scoring-timeout")
    case, attempt = _case_and_attempt(session, run)
    attempt.status = "scoring"
    attempt.phase_changed_at = now - timedelta(seconds=89)
    attempt.heartbeat_at = now
    session.commit()

    assert _sweep(session, now) == ZERO_COUNTS
    assert _reload(session, attempt).status == "scoring"

    attempt = _reload(session, attempt)
    attempt.phase_changed_at = now - timedelta(seconds=91)
    session.commit()
    counts = _sweep(session, now)
    assert counts[lifecycle.SCORING_TIMEOUT] == 1
    # 评分阶段超时属于用例失败，不是执行 timeout（不重跑 Agent）。
    _assert_terminal_timeout(
        session, run, case, attempt, lifecycle.SCORING_TIMEOUT, case_status="failed"
    )


def test_upload_timeout_uses_phase_changed_at(session: Session, lifecycle_settings) -> None:
    now = datetime.now(UTC)
    run = _make_run(session, key="upload-timeout")
    case, attempt = _case_and_attempt(session, run)
    attempt.status = "uploading"
    attempt.phase_changed_at = now - timedelta(seconds=74)
    attempt.heartbeat_at = now
    session.commit()

    assert _sweep(session, now) == ZERO_COUNTS
    assert _reload(session, attempt).status == "uploading"

    attempt = _reload(session, attempt)
    attempt.phase_changed_at = now - timedelta(seconds=76)
    session.commit()
    counts = _sweep(session, now)
    assert counts[lifecycle.UPLOAD_TIMEOUT] == 1
    _assert_terminal_timeout(
        session, run, case, attempt, lifecycle.UPLOAD_TIMEOUT, case_status="timeout"
    )


def test_heartbeat_timeout_is_terminal_when_attempts_are_exhausted(
    session: Session, lifecycle_settings
) -> None:
    now = datetime.now(UTC)
    run = _make_run(session, key="heartbeat-timeout", max_attempts=1)
    case, attempt = _case_and_attempt(session, run)
    attempt.status = "running"
    attempt.started_at = now - timedelta(seconds=10)
    attempt.heartbeat_at = now - timedelta(seconds=29)
    session.commit()

    assert _sweep(session, now) == ZERO_COUNTS

    attempt = _reload(session, attempt)
    attempt.heartbeat_at = now - timedelta(seconds=31)
    session.commit()
    counts = _sweep(session, now)
    assert counts[lifecycle.HEARTBEAT_TIMEOUT] == 1
    assert counts[lifecycle.EXECUTION_TIMEOUT] == 0
    _assert_terminal_timeout(
        session, run, case, attempt, lifecycle.HEARTBEAT_TIMEOUT, case_status="timeout"
    )


# ---------------------------------------------------------------------------
# Retry vs terminal settlement
# ---------------------------------------------------------------------------


def test_heartbeat_timeout_queues_a_retry_when_attempts_remain(
    session: Session, lifecycle_settings
) -> None:
    now = datetime.now(UTC)
    run = _make_run(session, key="heartbeat-retry", max_attempts=2)
    case, attempt = _case_and_attempt(session, run)
    attempt.status = "running"
    attempt.started_at = now - timedelta(seconds=10)
    attempt.heartbeat_at = now - timedelta(seconds=31)
    session.commit()

    counts = _sweep(session, now)
    assert counts[lifecycle.HEARTBEAT_TIMEOUT] == 1

    session.expire_all()
    attempts = (
        session.query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case.id)
        .order_by(EvalCaseAttempt.attempt_no)
        .all()
    )
    assert [row.attempt_no for row in attempts] == [1, 2]
    assert attempts[0].status == "timeout"  # old attempt preserved for diagnosis
    assert attempts[0].error_code == lifecycle.HEARTBEAT_TIMEOUT
    assert attempts[1].status == "pending"
    assert attempts[1].dispatch_key != attempts[0].dispatch_key

    case = session.get(EvalCaseRun, case.id)
    assert case.execution_status == "queued"
    assert case.latest_attempt_no == 2

    retries = [
        row
        for row in session.query(Outbox).all()
        if row.payload_json.get("attempt") == 2
    ]
    assert len(retries) == 1

    events = _case_events(session, case.id, attempt_no=1)
    assert [event.event_type for event in events] == ["case.failed", "case.retry_queued"]
    assert events[0].payload_json["retryable"] is True

    # The pending retry is not swept again by the same ``now``.
    assert _sweep(session, now) == ZERO_COUNTS


def test_timeout_at_max_attempts_is_terminal(session: Session, lifecycle_settings) -> None:
    now = datetime.now(UTC)
    run = _make_run(session, key="heartbeat-max", max_attempts=2)
    case, first = _case_and_attempt(session, run)
    first.status = "running"
    first.started_at = now - timedelta(seconds=10)
    first.heartbeat_at = now - timedelta(seconds=31)
    session.commit()
    assert _sweep(session, now)[lifecycle.HEARTBEAT_TIMEOUT] == 1

    # Attempt 2 is the last allowed attempt: heartbeat loss is now terminal.
    session.expire_all()
    _, second = _case_and_attempt(session, run, attempt_no=2)
    second.status = "running"
    second.started_at = now - timedelta(seconds=10)
    second.heartbeat_at = now - timedelta(seconds=31)
    session.commit()
    counts = _sweep(session, now)
    assert counts[lifecycle.HEARTBEAT_TIMEOUT] == 1

    session.expire_all()
    attempts = (
        session.query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case.id)
        .order_by(EvalCaseAttempt.attempt_no)
        .all()
    )
    assert [row.attempt_no for row in attempts] == [1, 2]  # no attempt 3
    assert attempts[1].status == "timeout"
    case = session.get(EvalCaseRun, case.id)
    assert case.execution_status == "timeout"
    events = _case_events(session, case.id, attempt_no=2)
    assert [event.event_type for event in events] == ["case.timeout"]


def test_sweep_is_idempotent(session: Session, lifecycle_settings) -> None:
    now = datetime.now(UTC)
    run = _make_run(
        session,
        key="sweep-idempotent",
        case_keys=("queued-case", "scoring-case", "heartbeat-case"),
        max_attempts=1,
    )
    _, queued = _case_and_attempt(session, run, "queued-case")
    queued.status = "queued"
    queued.phase_changed_at = now - timedelta(seconds=61)
    _, scoring = _case_and_attempt(session, run, "scoring-case")
    scoring.status = "scoring"
    scoring.phase_changed_at = now - timedelta(seconds=100)
    _, heartbeat = _case_and_attempt(session, run, "heartbeat-case")
    heartbeat.status = "running"
    heartbeat.started_at = now - timedelta(seconds=10)
    heartbeat.heartbeat_at = now - timedelta(seconds=31)
    session.commit()

    first = _sweep(session, now)
    assert first == {
        lifecycle.QUEUE_TIMEOUT: 1,
        lifecycle.EXECUTION_TIMEOUT: 0,
        lifecycle.SCORING_TIMEOUT: 1,
        lifecycle.UPLOAD_TIMEOUT: 0,
        lifecycle.HEARTBEAT_TIMEOUT: 1,
    }
    event_count = session.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).count()

    second = _sweep(session, now)
    assert second == ZERO_COUNTS
    assert session.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).count() == event_count
    assert _active_attempts(session, run) == []


def test_timeout_releases_the_runner_binding(session: Session, lifecycle_settings) -> None:
    now = datetime.now(UTC)
    run = _make_run(session, key="queue-release")
    case, attempt = _case_and_attempt(session, run)
    runner = runners.record_runner_heartbeat(
        session,
        runner_id="fixed-linux-a1",
        name="fixed-linux-a1",
        labels=["self-hosted", "linux", "eval"],
        status="online",
        current_case_attempt_id=None,
        now=now,
    )
    runner.status = "busy"
    runner.busy_case_attempt_id = case.id
    attempt.status = "queued"
    attempt.runner_id = runner.id
    attempt.phase_changed_at = now - timedelta(seconds=61)
    session.commit()

    assert _sweep(session, now)[lifecycle.QUEUE_TIMEOUT] == 1

    session.expire_all()
    runner = session.get(Runner, runner.id)
    assert runner.status == "online"
    assert runner.busy_case_attempt_id is None
    assert runner.last_heartbeat_at is not None


# ---------------------------------------------------------------------------
# Callback phase tracking
# ---------------------------------------------------------------------------


def test_callbacks_track_phase_changed_at(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]

    client.post(
        case_url(env, case_run_id, "started"),
        json=started_body(env, case_run_id),
        headers=auth(env),
    )
    env["session"].expire_all()  # callbacks commit in the app's session
    attempt = (
        env["session"]
        .query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case_run_id)
        .one()
    )
    assert attempt.status == "running"
    assert attempt.phase_changed_at is not None
    started_phase_at = attempt.phase_changed_at

    uploads = client.post(
        case_url(env, case_run_id, "uploads"), json=upload_body(env, case_run_id), headers=auth(env)
    )
    assert uploads.status_code == 200, uploads.text
    env["session"].expire_all()
    attempt = (
        env["session"]
        .query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case_run_id)
        .one()
    )
    assert attempt.status == "uploading"  # upload grace period is measurable
    assert attempt.phase_changed_at is not None
    assert attempt.phase_changed_at >= started_phase_at

from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox
from app.services import case_attempts
from sqlalchemy import select


def test_spot_interruption_retries_three_times_then_exhausts(eval_env) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    session = env["session"]
    run = session.get(EvalCaseRun, case_run_id).eval_run_id
    eval_run = session.get(EvalRun, run)
    assert eval_run is not None
    eval_run.max_attempts = 4
    session.commit()

    for _ in range(3):
        assert case_attempts.handle_spot_interruption(
            session, case_run_id=case_run_id
        ) is True

    assert case_attempts.handle_spot_interruption(
        session, case_run_id=case_run_id
    ) is False

    session.expire_all()
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .where(EvalCaseAttempt.eval_case_run_id == case_run_id)
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )
    assert [attempt.attempt_no for attempt in attempts] == [1, 2, 3, 4]
    assert all(attempt.error_code == "spot_interrupted" for attempt in attempts)

    case = session.get(EvalCaseRun, case_run_id)
    assert case is not None
    assert case.execution_status == "failed"
    assert case.error_code == "spot_retry_exhausted"

    retries = [
        row
        for row in session.scalars(select(Outbox))
        if row.payload_json.get("attempt") in (2, 3, 4)
    ]
    assert len(retries) == 3


def test_spot_interruption_respects_max_attempts(eval_env) -> None:
    """M8-44 (#306 P2-4): attempt_no must never exceed the declared cap."""

    env = eval_env
    case_run_id = env["cases"]["case-a"]
    session = env["session"]

    assert case_attempts.handle_spot_interruption(
        session, case_run_id=case_run_id
    ) is True
    # max_attempts defaults to 2: attempt 2 must not spawn attempt 3.
    assert case_attempts.handle_spot_interruption(
        session, case_run_id=case_run_id
    ) is False

    session.expire_all()
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .where(EvalCaseAttempt.eval_case_run_id == case_run_id)
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )
    assert [attempt.attempt_no for attempt in attempts] == [1, 2]
    case = session.get(EvalCaseRun, case_run_id)
    assert case is not None
    assert case.execution_status == "failed"
    assert case.error_code == "spot_interrupted"
    assert "max_attempts=2" in (case.error_summary or "")


def test_runner_lost_uses_normal_retry_budget_not_spot(eval_env) -> None:
    """M8-44 (#306 P1-1): runner_exited is not a spot reclaim."""

    env = eval_env
    case_run_id = env["cases"]["case-a"]
    session = env["session"]

    assert case_attempts.handle_runner_lost(
        session,
        case_run_id=case_run_id,
        reason="runner_exited",
        detail="last_heartbeat=2026-11-18T16:48:18+00:00",
        provider_state="Running",
        last_heartbeat="2026-11-18T16:48:18+00:00",
    ) is True

    session.expire_all()
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .where(EvalCaseAttempt.eval_case_run_id == case_run_id)
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )
    assert [attempt.attempt_no for attempt in attempts] == [1, 2]
    assert attempts[0].error_code == "runner_lost"
    assert "runner_exited" in (attempts[0].error_summary or "")
    assert "provider_state=Running" in (attempts[0].error_summary or "")
    assert attempts[1].status == "pending"
    assert not any(attempt.error_code == "spot_interrupted" for attempt in attempts)

    events = list(
        session.scalars(
            select(EvalEvent).where(EvalEvent.eval_case_run_id == case_run_id)
        )
    )
    assert not any(event.event_type == "case.spot_interrupted" for event in events)
    runner_lost = next(event for event in events if event.event_type == "case.runner_lost")
    assert runner_lost.payload_json["reason"] == "runner_exited"

    # runner_lost consumes the normal max_attempts budget, not spot retries.
    assert case_attempts.handle_runner_lost(
        session, case_run_id=case_run_id, reason="runner_heartbeat_lost"
    ) is False
    session.expire_all()
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .where(EvalCaseAttempt.eval_case_run_id == case_run_id)
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )
    assert [attempt.attempt_no for attempt in attempts] == [1, 2]
    case = session.get(EvalCaseRun, case_run_id)
    assert case is not None
    assert case.execution_status == "failed"
    assert case.error_code == "runner_lost"

from app.models import EvalCaseAttempt, EvalCaseRun, Outbox
from app.services import case_attempts
from sqlalchemy import select


def test_spot_interruption_retries_three_times_then_exhausts(eval_env) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    session = env["session"]

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

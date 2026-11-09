"""M4-02 A1: stable failure classification (retryable / permanent / scoring_only).

设计 06 §6.3：环境与传输故障可重试，配置错误快速失败，评分阶段失败不得
重跑 Agent。分类器只决定平台行为，不改变 Runner 上报的原始错误码。
"""

from __future__ import annotations

import pytest
from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox
from app.seed import ensure_default_scope
from app.services import case_attempts, evaluations, lifecycle
from case_helpers import failure_body
from sqlalchemy.orm import Session

# 稳定代码 → 分类；前缀规则与默认值在下方单独覆盖。
CLASSIFICATION_TABLE = {
    # retryable（精确）
    "heartbeat_timeout": "retryable",
    "spot_interrupted": "retryable",
    "runner_lost": "retryable",
    "network_error": "retryable",
    "model_rate_limited": "retryable",
    "model_unavailable": "retryable",
    "upload_failed": "retryable",
    "callback_retryable": "retryable",
    "service_unavailable": "retryable",
    # retryable（前缀）
    "network_timeout": "retryable",
    "network_reset": "retryable",
    "model_5xx": "retryable",
    "model_503": "retryable",
    "spot_reclaim_notice": "retryable",
    # permanent（精确）
    "config_error": "permanent",
    "credential_forbidden": "permanent",
    "credential_unavailable": "permanent",
    "commit_missing": "permanent",
    "asset_invalid": "permanent",
    "validation_error": "permanent",
    "callback_permanent": "permanent",
    "dispatch_failed": "permanent",
    # scoring_only
    "scoring_failed": "scoring_only",
    "scoring_timeout": "scoring_only",
    "artifact_missing": "scoring_only",
    # 默认 permanent（未知代码快速失败）
    "runtime_error": "permanent",
    "something_unexpected": "permanent",
    "": "permanent",
    None: "permanent",
}


@pytest.mark.parametrize(("error_code", "expected"), CLASSIFICATION_TABLE.items())
def test_classify_failure(error_code: str | None, expected: str) -> None:
    assert lifecycle.classify_failure(error_code) == expected


def test_failure_code_sets_do_not_overlap() -> None:
    assert not lifecycle.RETRYABLE_FAILURE_CODES & lifecycle.PERMANENT_FAILURE_CODES
    assert not lifecycle.RETRYABLE_FAILURE_CODES & lifecycle.SCORING_ONLY_FAILURE_CODES
    assert not lifecycle.PERMANENT_FAILURE_CODES & lifecycle.SCORING_ONLY_FAILURE_CODES
    assert lifecycle.QUEUE_TIMEOUT in lifecycle.TIMEOUT_CODES
    assert lifecycle.HEARTBEAT_TIMEOUT in lifecycle.TIMEOUT_CODES
    assert lifecycle.HEARTBEAT_TIMEOUT in lifecycle.RETRYABLE_FAILURE_CODES


# ---------------------------------------------------------------------------
# handle_failed honours the classification
# ---------------------------------------------------------------------------


def _make_run(
    session: Session, *, key: str, case_keys: tuple[str, ...] = ("case-a",), max_attempts: int = 2
) -> EvalRun:
    ensure_default_scope(session)
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="失败分类",
        case_keys=list(case_keys),
        idempotency_key=key,
        created_by="agent",
        settings=get_settings(),
    )
    run.max_attempts = max_attempts
    session.commit()
    return run


def _case_attempt(session: Session, run: EvalRun, case_key: str = "case-a") -> tuple[EvalCaseRun, EvalCaseAttempt]:
    case = (
        session.query(EvalCaseRun)
        .filter(EvalCaseRun.eval_run_id == run.id, EvalCaseRun.case_key == case_key)
        .one()
    )
    attempt = (
        session.query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case.id)
        .one()
    )
    return case, attempt


def _attempts(session: Session, case_run_id: str) -> list[EvalCaseAttempt]:
    session.expire_all()
    return (
        session.query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case_run_id)
        .order_by(EvalCaseAttempt.attempt_no)
        .all()
    )


def _case_events(session: Session, case_run_id: str) -> list[EvalEvent]:
    return (
        session.query(EvalEvent)
        .filter(
            EvalEvent.eval_case_run_id == case_run_id,
            EvalEvent.event_type.like("case.%"),
        )
        .order_by(EvalEvent.sequence)
        .all()
    )


def test_scoring_only_failure_is_terminal_without_rerunning_the_agent(session: Session) -> None:
    run = _make_run(session, key="classify-scoring", max_attempts=3)
    case, attempt = _case_attempt(session, run)

    result = case_attempts.handle_failed(
        session,
        payload=failure_body(case.id, retryable=True, error_code="scoring_failed"),
    )

    assert result["retry_queued"] is False
    assert result["status"] == "failed"
    attempts = _attempts(session, case.id)
    assert [row.attempt_no for row in attempts] == [1]  # Agent 不重跑
    assert attempts[0].status == "failed"
    assert attempts[0].error_code == "scoring_failed"
    assert attempts[0].retry_reason == "permanent"
    assert attempts[0].phase_changed_at is not None

    case = session.get(EvalCaseRun, case.id)
    assert case.execution_status == "failed"
    assert case.error_code == "scoring_failed"

    retries = [row for row in session.query(Outbox).all() if row.payload_json.get("attempt") == 2]
    assert retries == []
    assert [event.event_type for event in _case_events(session, case.id)] == ["case.failed"]


def test_retryable_failure_creates_one_attempt_but_permanent_does_not(session: Session) -> None:
    run = _make_run(session, key="classify-retry", case_keys=("retry-case", "fatal-case"))
    retry_case, _ = _case_attempt(session, run, "retry-case")
    fatal_case, _ = _case_attempt(session, run, "fatal-case")

    retried = case_attempts.handle_failed(
        session,
        payload=failure_body(retry_case.id, retryable=True, error_code="callback_retryable"),
    )
    assert retried["retry_queued"] is True
    retry_attempts = _attempts(session, retry_case.id)
    assert [row.attempt_no for row in retry_attempts] == [1, 2]
    assert retry_attempts[0].retry_reason == "retryable"
    retries = [row for row in session.query(Outbox).all() if row.payload_json.get("attempt") == 2]
    assert len(retries) == 1

    # 机理无关的失败（配置错误）即使 Runner 说可重试也只收口一次。
    fatal = case_attempts.handle_failed(
        session,
        payload=failure_body(fatal_case.id, retryable=False, error_code="config_error"),
    )
    assert fatal["retry_queued"] is False
    assert [row.attempt_no for row in _attempts(session, fatal_case.id)] == [1]
    attempts = _attempts(session, fatal_case.id)
    assert attempts[0].status == "failed"
    assert attempts[0].retry_reason == "permanent"
    case = session.get(EvalCaseRun, fatal_case.id)
    assert case.execution_status == "failed"
    assert case.error_code == "config_error"


def test_failed_event_payload_keeps_the_callback_shape(session: Session) -> None:
    run = _make_run(session, key="classify-event")
    case, _ = _case_attempt(session, run)
    body = failure_body(case.id, retryable=True, error_code="callback_retryable")

    case_attempts.handle_failed(session, payload=body)

    events = _case_events(session, case.id)
    assert [event.event_type for event in events] == ["case.failed", "case.retry_queued"]
    failed = events[0]
    assert failed.sequence == body["sequence"]
    assert failed.payload_json == {
        "event_id": body["event_id"],
        "error_code": "callback_retryable",
        "retryable": True,
        "error_summary": body["error_summary"][:200],
    }
    retry_queued = events[1]
    assert retry_queued.sequence == body["sequence"] + 1
    assert set(retry_queued.payload_json) == {"new_attempt_no", "shard_id", "dispatch_key"}

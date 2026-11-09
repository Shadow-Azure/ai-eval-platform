"""M4-02 A2: cancellation semantics and stateless-token revocation.

验收对照：

- 取消只能终结「尚未启动 Agent」的 attempt；running/scoring/uploading 的
  attempt 必须留给回调/宽限期，否则已完成的产物会被丢弃；
- 没有活跃 attempt 时立即 `cancelled`，否则停在 `cancelling` 由 reconciler
  在宽限期后收口；
- 取消后 `started` 必须 409 `run_cancelled` 并留下审计事件；
- 模型网关 token 无法单独撤销，因此在 claims 绑定校验处按 run 状态拒绝
  （409 `run_cancelled`），不需要新的 token 存储。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.defaults import DEFAULT_TENANT_ID
from app.model_gateway.tokens import GatewayClaims, issue_gateway_token
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox
from app.seed import ensure_default_scope
from app.services import case_attempts, dispatcher, evaluations, reconciler
from case_helpers import auth, case_url, started_body
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

CANCEL_EVENTS = ("eval.cancelling", "eval.cancelled")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def _create_run(
    session: Session,
    *,
    key: str,
    case_keys: tuple[str, ...] = ("case-a", "case-b"),
) -> EvalRun:
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="取消冒烟",
        case_keys=list(case_keys),
        idempotency_key=key,
        created_by="agent",
        settings=get_settings(),
    )
    return run


def _case(session: Session, run: EvalRun, case_key: str) -> EvalCaseRun:
    return (
        session.query(EvalCaseRun)
        .filter(EvalCaseRun.eval_run_id == run.id, EvalCaseRun.case_key == case_key)
        .one()
    )


def _attempt(session: Session, case: EvalCaseRun, attempt_no: int = 1) -> EvalCaseAttempt:
    return (
        session.query(EvalCaseAttempt)
        .filter(
            EvalCaseAttempt.eval_case_run_id == case.id,
            EvalCaseAttempt.attempt_no == attempt_no,
        )
        .one()
    )


def _make_running(
    session: Session, run: EvalRun, case: EvalCaseRun, *, now: datetime
) -> EvalCaseAttempt:
    """Simulate a started attempt without going through the callback API."""

    attempt = _attempt(session, case)
    attempt.status = "running"
    attempt.started_at = now - timedelta(seconds=30)
    attempt.heartbeat_at = now - timedelta(seconds=5)
    attempt.phase_changed_at = now
    case.execution_status = "running"
    run.status = "running"
    if run.started_at is None:
        run.started_at = now
    session.commit()
    return attempt


def _run_events(session: Session, run_id: str, event_type: str) -> list[EvalEvent]:
    return list(
        session.scalars(
            select(EvalEvent)
            .where(
                EvalEvent.eval_run_id == run_id,
                EvalEvent.event_type == event_type,
                EvalEvent.eval_case_run_id.is_(None),
            )
            .order_by(EvalEvent.id)
        )
    )


def _case_events(session: Session, case_run_id: str, event_type: str) -> list[EvalEvent]:
    return list(
        session.scalars(
            select(EvalEvent).where(
                EvalEvent.eval_case_run_id == case_run_id,
                EvalEvent.event_type == event_type,
            )
        )
    )


def _attempts(session: Session, run: EvalRun) -> list[EvalCaseAttempt]:
    return (
        session.query(EvalCaseAttempt)
        .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
        .filter(EvalCaseRun.eval_run_id == run.id)
        .order_by(EvalCaseAttempt.id)
        .all()
    )


def _completion_payload(
    case: EvalCaseRun, *, attempt_no: int = 1, event_id: str = "evt-reconciled-complete"
) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "case_run_id": case.id,
        "attempt": attempt_no,
        "sequence": 2,
        "execution_status": "succeeded",
        "result_status": "resolved",
        "score_manifest": {
            "object_key": f"evaluations/{case.eval_run_id}/score.json",
            "sha256": "a" * 64,
            "size": 12,
        },
        "summary": {"duration_ms": 1000},
    }


class NoopTransport:
    """Dispatcher transport that must not be called for a cancelled run."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url: str, **_kwargs):
        self.calls.append(url)
        return 204, ""


class _FakeGitHub:
    """Minimal cancel-endpoint transport (lookup is avoided via github_run_id)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url: str, *, method: str, body, headers, timeout: float):
        self.calls.append({"url": url, "method": method})
        return 202, ""


# ---------------------------------------------------------------------------
# cancel_evaluation state machine
# ---------------------------------------------------------------------------


def test_cancel_queued_only_finalizes_immediately(seeded: Session, configure_settings) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = _create_run(seeded, key="cancel-queued")
    now = datetime.now(UTC)

    cancelled, first = evaluations.cancel_evaluation(seeded, run_id=run.id, now=now)
    assert first is True

    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.status == "cancelled"
    assert run.finished_at is not None
    assert run.cancel_requested_at is not None
    assert run.queued_count == 0 and run.running_count == 0
    for attempt in _attempts(seeded, run):
        assert attempt.status == "cancelled"
        assert attempt.error_code == "run_cancelled"
        assert attempt.ended_at is not None
    for case in seeded.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id):
        assert case.execution_status == "cancelled"
    for row in seeded.query(Outbox).all():
        assert row.status == "cancelled"
        assert row.payload_json["last_error"] == "run_cancelled"

    assert len(_run_events(seeded, run.id, "eval.cancelling")) == 1
    assert len(_run_events(seeded, run.id, "eval.cancelled")) == 1

    # Replay is a no-op: no duplicate events, no state change.
    _, replayed = evaluations.cancel_evaluation(seeded, run_id=run.id, now=now)
    assert replayed is False
    assert len(_run_events(seeded, run.id, "eval.cancelling")) == 1
    assert len(_run_events(seeded, run.id, "eval.cancelled")) == 1


def test_cancel_keeps_running_attempt_and_blocks_new_dispatch(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    now = datetime.now(UTC)
    run = _create_run(seeded, key="cancel-running")
    running_case = _case(seeded, run, "case-a")
    queued_case = _case(seeded, run, "case-b")
    running_attempt = _make_running(seeded, run, running_case, now=now)

    evaluations.cancel_evaluation(seeded, run_id=run.id, now=now)

    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    running_attempt = seeded.get(EvalCaseAttempt, running_attempt.id)
    queued_attempt = _attempt(seeded, queued_case)
    assert run.status == "cancelling"  # waits for the started attempt to settle
    assert run.finished_at is None
    assert running_attempt.status == "running"  # untouched: it owns artifacts
    assert queued_attempt.status == "cancelled"
    assert queued_attempt.error_code == "run_cancelled"
    assert seeded.get(EvalCaseRun, queued_case.id).execution_status == "cancelled"
    assert seeded.get(EvalCaseRun, running_case.id).execution_status == "running"

    # A cancelled run must not dispatch anything new.
    transport = NoopTransport()
    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport)
    assert outcome.dispatched == 0
    assert transport.calls == []
    assert all(row.status == "cancelled" for row in seeded.query(Outbox).all())


def test_running_attempt_completion_finalizes_cancelled_run(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    now = datetime.now(UTC)
    run = _create_run(seeded, key="cancel-complete")
    running_case = _case(seeded, run, "case-a")
    running_attempt = _make_running(seeded, run, running_case, now=now)
    evaluations.cancel_evaluation(seeded, run_id=run.id, now=now)

    # The in-flight attempt is allowed to report its completion; the run stays
    # cancelled (never completed) but the artifact is kept.
    case_attempts.handle_completed(
        seeded,
        payload=_completion_payload(running_case),
        now=now + timedelta(seconds=10),
        settings=get_settings(),
    )

    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    attempt = seeded.get(EvalCaseAttempt, running_attempt.id)
    case = seeded.get(EvalCaseRun, running_case.id)
    assert attempt.status == "succeeded"
    assert attempt.error_code is None
    assert case.execution_status == "succeeded"
    assert case.result_status == "resolved"
    assert run.status == "cancelled"  # never completed/partial
    assert run.finished_at is not None
    assert len(_run_events(seeded, run.id, "eval.cancelled")) == 1
    assert len(_case_events(seeded, case.id, "case.completed")) == 1

    # The reconciler then sees no cancelling run and makes no further change.
    before = seeded.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).count()
    counts = reconciler.reconcile(seeded, settings=get_settings(), now=now + timedelta(seconds=11))
    assert counts["cancelled"] == 0
    assert seeded.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).count() == before


def test_cancel_grace_exceeded_force_cancels_active_attempt(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token", EVAL_CANCEL_GRACE_SECONDS="60")
    now = datetime.now(UTC)
    run = _create_run(seeded, key="cancel-grace", case_keys=("case-a",))
    running_case = _case(seeded, run, "case-a")
    running_attempt = _make_running(seeded, run, running_case, now=now)
    run.github_run_id = "4242"
    seeded.commit()
    evaluations.cancel_evaluation(seeded, run_id=run.id, now=now)

    transport = _FakeGitHub()
    counts = reconciler.reconcile(
        seeded,
        settings=get_settings(),
        now=now + timedelta(seconds=59),
        transport=transport,
    )
    assert counts["cancelled"] == 0  # still inside the grace window
    assert seeded.get(EvalCaseAttempt, running_attempt.id).status == "running"

    counts = reconciler.reconcile(
        seeded,
        settings=get_settings(),
        now=now + timedelta(seconds=61),
        transport=transport,
    )
    assert counts["cancelled"] == 1

    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    attempt = seeded.get(EvalCaseAttempt, running_attempt.id)
    case = seeded.get(EvalCaseRun, running_case.id)
    assert run.status == "cancelled"
    assert run.finished_at is not None
    assert attempt.status == "cancelled"
    assert attempt.error_code == "cancel_grace_exceeded"
    assert case.execution_status == "cancelled"
    assert case.error_code == "cancel_grace_exceeded"
    assert len(_run_events(seeded, run.id, "eval.cancelled")) == 1

    # Second pass is idempotent: already terminal, no new events/cancels.
    events_before = seeded.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).count()
    again = reconciler.reconcile(
        seeded,
        settings=get_settings(),
        now=now + timedelta(seconds=120),
        transport=transport,
    )
    assert again["cancelled"] == 0
    assert seeded.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).count() == events_before


# ---------------------------------------------------------------------------
# Revocation points: started callback and model gateway token
# ---------------------------------------------------------------------------


def test_started_after_cancel_is_409_run_cancelled_with_audit(
    eval_env, client: TestClient
) -> None:
    env = eval_env
    session: Session = env["session"]
    now = datetime.now(UTC)
    run = session.get(EvalRun, env["run_id"])
    running_case_id = env["cases"]["case-a"]
    queued_case_id = env["cases"]["case-b"]
    _make_running(session, run, session.get(EvalCaseRun, running_case_id), now=now)
    evaluations.cancel_evaluation(session, run_id=run.id, now=now)

    response = client.post(
        case_url(env, queued_case_id, "started"),
        json=started_body(env, queued_case_id),
        headers=auth(env),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "run_cancelled"

    session.expire_all()
    rejected = _case_events(session, queued_case_id, "case.started_rejected")
    assert len(rejected) == 1
    assert rejected[0].payload_json["reason"] == "run_cancelled"
    assert rejected[0].payload_json["run_status"] == "cancelling"
    attempt = _attempt(session, session.get(EvalCaseRun, queued_case_id))
    assert attempt.status == "cancelled"  # no mutation, no resurrect

    # The runner retry of the same event is rejected again without new audit rows.
    replay = client.post(
        case_url(env, queued_case_id, "started"),
        json=started_body(env, queued_case_id),
        headers=auth(env),
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["error"]["code"] == "run_cancelled"
    session.expire_all()
    assert len(_case_events(session, queued_case_id, "case.started_rejected")) == 1


def test_model_gateway_rejects_token_for_cancelling_run(
    seeded: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(GATEWAY_TOKEN_SECRET="test-gateway-secret")
    now = datetime.now(UTC)
    run = _create_run(seeded, key="cancel-gateway", case_keys=("case-a",))
    running_case = _case(seeded, run, "case-a")
    attempt = _make_running(seeded, run, running_case, now=now)
    evaluations.cancel_evaluation(seeded, run_id=run.id, now=now)
    seeded.expire_all()
    assert seeded.get(EvalRun, run.id).status == "cancelling"

    token = issue_gateway_token(
        GatewayClaims(
            tenant_id=str(DEFAULT_TENANT_ID),
            model_config_id="config-not-resolved",
            allowed_models=("gpt-test",),
            eval_run_id=run.id,
            shard_id=attempt.shard_id,
            attempt=1,
        ),
        "test-gateway-secret",
    )
    response = client.post(
        "/model-gateway/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_cancelled"

    # Once the run is terminal the same token stays revoked.
    seeded.get(EvalRun, run.id).status = "cancelled"
    seeded.commit()
    again = client.post(
        "/model-gateway/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "run_cancelled"

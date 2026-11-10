"""M4-05: model-budget pause and resume (issue #82, design 06 §8).

验收对照：

- 达到预算后不再派发新分片/新 attempt，网关拒绝新模型调用，run 进入
  ``pausing_budget``；未启动 attempt 立即收口，已在执行的 attempt 可以继续
  上传/回调，宽限期结束后由 reconciler 强制收口；
- 活跃 attempt 全部收口后 run 进入 ``paused_budget``（非终态，不写最终指标），
  API 返回预算、已花费、已完成/未完成数量；
- ``resume-budget`` 原子增加预算与 ``budget_version``、写审计，只为未完成用例
  创建新 attempt；重复/版本不匹配请求被拒绝且不会重复加预算；
- 暂停期间不自动换模型/资源，已完成用例结果与成本不变。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.config import get_settings
from app.models import (
    AuditLog,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    ModelUsage,
    Outbox,
)
from app.seed import ensure_default_scope
from app.services import budget, case_attempts, dispatcher, evaluations, reconciler
from app.services.case_attempts import CaseCallbackError
from case_helpers import (
    auth,
    case_url,
    completion_body,
    failure_body,
    started_body,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

BUDGET_EVENTS = ("eval.budget_pausing", "eval.budget_paused", "eval.budget_resumed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def _model_config(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "openai",
            "display_name": "预算测试模型",
            "base_url": "https://provider.test/v1",
            "model_name": "gpt-test",
            "secret": "sk-budget-test-123456",
            "pricing": {"input_per_1m": 1_000, "output_per_1m": 2_000},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_run(
    session: Session,
    client: TestClient,
    *,
    key: str,
    case_keys: tuple[str, ...] = ("case-a", "case-b"),
    budget_usd: str = "10",
    shard_size: int | None = None,
) -> tuple[EvalRun, dict]:
    config = _model_config(client)
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="预算暂停冒烟",
        case_keys=list(case_keys),
        shard_size=shard_size,
        idempotency_key=key,
        created_by="agent",
        model_config_id=config["id"],
        model_budget_usd=Decimal(budget_usd),
        settings=get_settings(),
    )
    return run, config


def _add_usage(session: Session, run: EvalRun, config: dict, cost: str) -> None:
    session.add(
        ModelUsage(
            tenant_id=run.tenant_id,
            model_config_id=config["id"],
            eval_run_id=run.id,
            status="succeeded",
            cost_usd=Decimal(cost),
        )
    )
    session.commit()


def _attempts(session: Session, case: EvalCaseRun) -> list[EvalCaseAttempt]:
    return list(
        session.scalars(
            select(EvalCaseAttempt)
            .where(EvalCaseAttempt.eval_case_run_id == case.id)
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )


def _case(session: Session, run: EvalRun, key: str) -> EvalCaseRun:
    return session.scalar(
        select(EvalCaseRun).where(
            EvalCaseRun.eval_run_id == run.id, EvalCaseRun.case_key == key
        )
    )


def _run_events(session: Session, run_id: str, event_type: str) -> list[EvalEvent]:
    return list(
        session.scalars(
            select(EvalEvent).where(
                EvalEvent.eval_run_id == run_id,
                EvalEvent.event_type == event_type,
                EvalEvent.eval_case_run_id.is_(None),
            )
        )
    )


def _start(client: TestClient, env: dict, case_key: str) -> None:
    response = client.post(
        case_url(env, env["cases"][case_key], "started"),
        headers=auth(env),
        json=started_body(env, env["cases"][case_key]),
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Pause entry: stop dispatch, settle not-started attempts
# ---------------------------------------------------------------------------


def test_over_budget_pauses_run_parks_dispatch_and_keeps_cases_unfinished(
    seeded: Session, client: TestClient
) -> None:
    run, config = _create_run(seeded, client, key="pause-entry", shard_size=1)
    _add_usage(seeded, run, config, "10.5")
    now = datetime.now(UTC)

    counts = budget.reconcile_budget_pauses(seeded, settings=get_settings(), now=now)
    assert counts["budget_paused"] == 1
    assert counts["budget_settled"] == 1

    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.status == "paused_budget"
    assert run.status not in evaluations.TERMINAL_RUN_STATUSES
    assert run.pause_reason == "budget_exceeded"
    assert run.budget_spent_usd == Decimal("10.5")
    assert run.finished_at is None  # pause is not a terminal result

    for case in seeded.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id):
        assert case.execution_status == "queued"  # unfinished, resumable
        for attempt in _attempts(seeded, case):
            assert attempt.status == "cancelled"
            assert attempt.error_code == "budget_paused"

    for row in seeded.query(Outbox).filter(Outbox.aggregate_id == run.id):
        assert row.status == "cancelled"
        assert row.payload_json["last_error"] == "budget_paused"

    assert len(_run_events(seeded, run.id, "eval.budget_pausing")) == 1
    assert len(_run_events(seeded, run.id, "eval.budget_paused")) == 1

    summary = evaluations.evaluation_summary(
        run, spent_usd=evaluations.spent_usd(seeded, run.id)
    )
    assert Decimal(summary["model_budget_usd"]) == Decimal("10")
    assert Decimal(summary["budget_spent_usd"]) == Decimal("10.5")
    assert Decimal(summary["budget_remaining_usd"]) == Decimal("0")
    assert summary["finished_count"] == 0
    assert summary["unfinished_count"] == 2
    assert summary["pause_reason"] == "budget_exceeded"

    # The dispatcher must not send the parked rows.
    calls: list[str] = []

    def transport(url: str, **_kwargs):
        calls.append(url)
        return 204, ""

    outcome = dispatcher.dispatch_pending_outbox(seeded, transport=transport, now=now)
    assert outcome.dispatched == 0
    assert calls == []


def test_pause_rejects_started_and_settles_budget_interrupted_attempt(
    seeded: Session, client: TestClient, eval_env: dict
) -> None:
    session = eval_env["session"]
    now = datetime.now(UTC)
    run = session.get(EvalRun, eval_env["run_id"])
    run.model_budget_usd = Decimal("10")
    session.commit()

    _start(client, eval_env, "case-a")

    assert budget.pause_for_budget(session, run, spent=Decimal("10"), now=now)
    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.status == "pausing_budget"

    # The queued case-b attempt is settled at pause entry; case-b stays queued.
    case_b = _case(session, run, "case-b")
    assert _attempts(session, case_b)[0].status == "cancelled"
    assert case_b.execution_status == "queued"

    # A queued Job that tries to start after the pause is rejected + audited.
    # Exercised at the service layer: the HTTP callback route first rejects a
    # second job for the single fixed runner as busy, which is orthogonal here.
    with pytest.raises(CaseCallbackError) as rejected:
        case_attempts.handle_started(
            session,
            payload=started_body(eval_env, eval_env["cases"]["case-b"]),
            now=now,
        )
    assert rejected.value.status_code == 409
    assert rejected.value.code == "budget_paused"
    rejected_events = list(
        session.scalars(
            select(EvalEvent).where(
                EvalEvent.eval_case_run_id == eval_env["cases"]["case-b"],
                EvalEvent.event_type == "case.started_rejected",
            )
        )
    )
    assert rejected_events and rejected_events[0].payload_json["reason"] == "budget_paused"

    # The already-running case-a reports the gateway's budget refusal. The
    # runner contract has a closed error_code enum, so the interruption arrives
    # as ``runtime_error``; the paused run is what keeps the case unfinished.
    failed = client.post(
        case_url(eval_env, eval_env["cases"]["case-a"], "failed"),
        headers=auth(eval_env),
        json=failure_body(
            eval_env["cases"]["case-a"], error_code="runtime_error", retryable=False
        ),
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["retry_queued"] is False

    session.expire_all()
    case_a = _case(session, run, "case-a")
    attempt_a = _attempts(session, case_a)[0]
    assert attempt_a.status == "cancelled"
    assert attempt_a.error_code == "runtime_error"
    assert case_a.execution_status == "queued"  # kept for resume
    assert len(_attempts(session, case_a)) == 1  # no retry attempt during pause

    counts = budget.reconcile_budget_pauses(
        session, settings=get_settings(), now=now + timedelta(seconds=5)
    )
    assert counts["budget_settled"] == 1
    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.status == "paused_budget"
    assert run.queued_count == 2 and run.running_count == 0


def test_pause_grace_force_settles_still_running_attempt(
    seeded: Session, client: TestClient, eval_env: dict
) -> None:
    session = eval_env["session"]
    now = datetime.now(UTC)
    run = session.get(EvalRun, eval_env["run_id"])
    run.model_budget_usd = Decimal("10")
    session.commit()
    _start(client, eval_env, "case-a")

    assert budget.pause_for_budget(session, run, spent=Decimal("10"), now=now)
    # Before the grace expires the running attempt may still settle itself.
    budget.reconcile_budget_pauses(
        session, settings=get_settings(), now=now + timedelta(seconds=5)
    )
    session.expire_all()
    assert session.get(EvalRun, run.id).status == "pausing_budget"

    budget.reconcile_budget_pauses(
        session,
        settings=get_settings(),
        now=now + timedelta(seconds=get_settings().eval_budget_pause_grace_seconds + 1),
    )
    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.status == "paused_budget"
    attempt = _attempts(session, _case(session, run, "case-a"))[0]
    assert attempt.status == "cancelled"
    assert attempt.error_code == "budget_paused"
    assert _case(session, run, "case-a").execution_status == "queued"


# ---------------------------------------------------------------------------
# Resume: atomic budget + version, only unfinished cases
# ---------------------------------------------------------------------------


def _paused_run(
    session: Session, client: TestClient, *, key: str
) -> tuple[EvalRun, dict]:
    run, config = _create_run(session, client, key=key)
    _add_usage(session, run, config, "10")
    now = datetime.now(UTC)
    assert budget.pause_for_budget(session, run, spent=Decimal("10"), now=now)
    assert budget.finalize_paused(session, run, now=now)
    session.expire_all()
    return session.get(EvalRun, run.id), config


def test_resume_adds_budget_and_only_reschedules_unfinished_cases(
    seeded: Session, client: TestClient
) -> None:
    now = datetime.now(UTC)
    run, _config = _paused_run(seeded, client, key="resume-main")

    # Pretend case-a already finished before the pause: it must not rerun.
    case_a = _case(seeded, run, "case-a")
    case_a.execution_status = "succeeded"
    case_a.result_status = "resolved"
    attempt_a = _attempts(seeded, case_a)[0]
    attempt_a.status = "succeeded"
    attempt_a.ended_at = now
    seeded.commit()
    evaluations.refresh_run_progress(seeded, run, now=now)
    seeded.commit()

    response = client.post(
        f"/api/v1/evaluations/{run.id}/resume-budget",
        json={"additional_budget_usd": 5, "budget_version": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resumed"] is True
    assert body["attempts_created"] == 1
    assert body["status"] == "queued"
    assert Decimal(body["model_budget_usd"]) == Decimal("15")
    assert Decimal(body["budget_spent_usd"]) == Decimal("10")
    assert body["budget_version"] == 2
    assert body["pause_reason"] is None
    assert body["unfinished_count"] == 1
    assert body["finished_count"] == 1

    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.model_budget_usd == Decimal("15")
    assert run.budget_version == 2
    assert run.status == "queued"

    case_a = _case(seeded, run, "case-a")
    assert case_a.execution_status == "succeeded"
    assert [attempt.attempt_no for attempt in _attempts(seeded, case_a)] == [1]

    case_b = _case(seeded, run, "case-b")
    attempts_b = _attempts(seeded, case_b)
    assert [attempt.attempt_no for attempt in attempts_b] == [1, 2]
    assert attempts_b[-1].status == "pending"
    assert case_b.latest_attempt_no == 2

    pending = list(
        seeded.scalars(
            select(Outbox).where(
                Outbox.aggregate_id == run.id, Outbox.status == "pending"
            )
        )
    )
    assert len(pending) == 1
    assert "resume_b2" in pending[0].dispatch_key

    audit = seeded.scalars(
        select(AuditLog).where(AuditLog.action == "eval.budget_resumed")
    ).all()
    assert len(audit) == 1
    assert audit[0].metadata_json["additional_budget_usd"] == "5"
    assert len(_run_events(seeded, run.id, "eval.budget_resumed")) == 1

    # A replayed/parallel request with the same version must NOT add twice.
    replay = client.post(
        f"/api/v1/evaluations/{run.id}/resume-budget",
        json={"additional_budget_usd": 5, "budget_version": 1},
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["error"]["code"] == "budget_version_conflict"
    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.model_budget_usd == Decimal("15")
    assert run.budget_version == 2
    assert len(seeded.scalars(select(Outbox)).all()) == 2  # old cancelled + new pending


def test_resume_rejects_stale_version_while_paused(
    seeded: Session, client: TestClient
) -> None:
    run, _config = _paused_run(seeded, client, key="resume-version")
    response = client.post(
        f"/api/v1/evaluations/{run.id}/resume-budget",
        json={"additional_budget_usd": 5, "budget_version": 9},
    )
    assert response.status_code == 409
    error = response.json()["detail"]["error"]
    assert error["code"] == "budget_version_conflict"
    assert error["details"]["current_budget_version"] == 1
    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.model_budget_usd == Decimal("10")
    assert run.budget_version == 1


def test_resume_rejects_non_paused_and_requires_positive_budget(
    seeded: Session, client: TestClient
) -> None:
    run, _config = _create_run(seeded, client, key="resume-not-paused")
    response = client.post(
        f"/api/v1/evaluations/{run.id}/resume-budget",
        json={"additional_budget_usd": 5, "budget_version": 1},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "evaluation_not_budget_paused"

    paused, _ = _paused_run(seeded, client, key="resume-invalid-amount")
    invalid = client.post(
        f"/api/v1/evaluations/{paused.id}/resume-budget",
        json={"additional_budget_usd": 0, "budget_version": 1},
    )
    assert invalid.status_code == 422


def test_resume_waits_for_pause_to_settle_active_attempts(
    seeded: Session, client: TestClient, eval_env: dict
) -> None:
    session = eval_env["session"]
    now = datetime.now(UTC)
    run = session.get(EvalRun, eval_env["run_id"])
    run.model_budget_usd = Decimal("10")
    session.commit()
    _start(client, eval_env, "case-a")
    assert budget.pause_for_budget(session, run, spent=Decimal("10"), now=now)

    response = client.post(
        f"/api/v1/evaluations/{run.id}/resume-budget",
        json={"additional_budget_usd": 5, "budget_version": 1},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "budget_pause_in_progress"


# ---------------------------------------------------------------------------
# Non-terminal pause semantics
# ---------------------------------------------------------------------------


def test_cancel_after_pause_finalizes_instead_of_hanging(
    seeded: Session, client: TestClient
) -> None:
    run, _config = _paused_run(seeded, client, key="cancel-paused")
    cancelled, _changed = evaluations.cancel_evaluation(
        seeded, run_id=run.id, now=datetime.now(UTC)
    )
    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.status == "cancelled"
    assert run.finished_at is not None
    for case in seeded.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id):
        assert case.execution_status == "cancelled"


def test_no_final_metrics_are_written_while_paused(
    seeded: Session,
    client: TestClient,
    eval_env: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = eval_env["session"]
    now = datetime.now(UTC)
    run = session.get(EvalRun, eval_env["run_id"])
    run.model_budget_usd = Decimal("10")
    # Non-null dataset_version_id forces the metric recompute branch; no
    # definitions exist, so the spy only records the finality flag.
    run.dataset_version_id = "dsv-budget-test"
    session.commit()
    _start(client, eval_env, "case-a")
    assert budget.pause_for_budget(session, run, spent=Decimal("10"), now=now)

    captured: list[bool] = []
    monkeypatch.setattr(
        "app.services.case_attempts.ingest_score_report", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.case_attempts.recompute_metrics",
        lambda *args, **kwargs: captured.append(bool(kwargs.get("is_final"))),
    )

    # A readable score object is required: ObjectMissing/parse failure would
    # return before the metric branch this test inspects.
    content = b'{"case-a": {"resolved": true}}'
    object_key = f"evaluations/{run.id}/budget-score.json"
    eval_env["store"].add_bytes(object_key, content)
    body = completion_body(eval_env, eval_env["cases"]["case-a"])
    body["score_manifest"] = {
        "object_key": object_key,
        "sha256": "a" * 64,
        "size": len(content),
    }
    done = client.post(
        case_url(eval_env, eval_env["cases"]["case-a"], "completed"),
        headers=auth(eval_env),
        json=body,
    )
    assert done.status_code == 200, done.text
    assert captured == [False]

    budget.reconcile_budget_pauses(
        session, settings=get_settings(), now=now + timedelta(seconds=5)
    )
    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.status == "paused_budget"
    assert captured == [False]  # paused state never writes a final metric

    # After resume, completing the last unfinished case DOES persist a final
    # metric: the pause must not suppress the true terminal result.
    resumed = client.post(
        f"/api/v1/evaluations/{run.id}/resume-budget",
        json={"additional_budget_usd": 5, "budget_version": 1},
    )
    assert resumed.status_code == 200, resumed.text
    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.status == "queued"
    final_content = b'{"case-b": {"resolved": true}}'
    final_key = f"evaluations/{run.id}/budget-final-score.json"
    eval_env["store"].add_bytes(final_key, final_content)
    final_body = completion_body(eval_env, eval_env["cases"]["case-b"], attempt=2)
    final_body["score_manifest"] = {
        "object_key": final_key,
        "sha256": "b" * 64,
        "size": len(final_content),
    }
    case_attempts.handle_completed(
        session, payload=final_body, now=datetime.now(UTC), settings=get_settings()
    )
    session.expire_all()
    run = session.get(EvalRun, run.id)
    assert run.status == "completed"
    assert captured == [False, True]


def test_budget_events_are_recorded_once(seeded: Session, client: TestClient) -> None:
    run, config = _create_run(seeded, client, key="pause-events")
    _add_usage(seeded, run, config, "10")
    now = datetime.now(UTC)
    counts = reconciler.reconcile(seeded, settings=get_settings(), now=now)
    assert counts["budget_paused"] == 1
    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.status == "paused_budget"
    for event_type in BUDGET_EVENTS[:2]:
        assert len(_run_events(seeded, run.id, event_type)) == 1
    assert seeded.scalar(
        select(func.count()).select_from(EvalEvent).where(
            EvalEvent.eval_run_id == run.id,
            EvalEvent.event_type == "eval.budget_resumed",
        )
    ) == 0


def test_second_pause_cycle_records_new_events_and_grace_clock(
    seeded: Session, client: TestClient
) -> None:
    """A resume at version 2 that spends through the new budget must pause again.

    The pause/grace event dedupe is scoped by budget_version, otherwise the
    second cycle would reuse the first event (and its expired grace clock).
    """

    run, config = _paused_run(seeded, client, key="repeat-pause")
    first = client.post(
        f"/api/v1/evaluations/{run.id}/resume-budget",
        json={"additional_budget_usd": 5, "budget_version": 1},
    )
    assert first.status_code == 200, first.text
    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.budget_version == 2

    _add_usage(seeded, run, config, "5")  # total spend 15 == new budget 15
    counts = budget.reconcile_budget_pauses(
        seeded, settings=get_settings(), now=datetime.now(UTC)
    )
    assert counts["budget_paused"] == 1
    assert counts["budget_settled"] == 1

    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.status == "paused_budget"
    assert run.budget_version == 2
    assert len(_run_events(seeded, run.id, "eval.budget_pausing")) == 2
    assert len(_run_events(seeded, run.id, "eval.budget_paused")) == 2

    second = client.post(
        f"/api/v1/evaluations/{run.id}/resume-budget",
        json={"additional_budget_usd": 1, "budget_version": 2},
    )
    assert second.status_code == 200, second.text
    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.budget_version == 3
    assert len(_run_events(seeded, run.id, "eval.budget_resumed")) == 2

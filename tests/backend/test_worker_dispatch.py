"""Worker loop consumes the shard-dispatch outbox each poll (M2-05/M4-02)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalRun, Outbox
from app.seed import ensure_default_scope
from app.services import evaluations
from app.worker.main import dispatch_once, reconcile_once
from sqlalchemy.orm import sessionmaker


def test_worker_poll_processes_dispatch_outbox(
    engine, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    factory: sessionmaker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as session:
        ensure_default_scope(session)
        evaluations.create_synthetic_evaluation(
            session,
            name="worker 冒烟",
            case_keys=["w1"],
            idempotency_key="worker-1",
            created_by="agent",
            settings=get_settings(),
        )

    calls: list[str] = []

    def transport(url: str, **_kwargs):
        calls.append(url)
        return 204, ""

    with factory() as session:
        outcome = dispatch_once(session, transport=transport)
    assert outcome.dispatched == 1
    assert len(calls) == 1

    with factory() as session:
        row = session.query(Outbox).one()
        assert row.status == "sent"
        run = evaluations.get_evaluation(session, row.aggregate_id)
        assert run.status == "queued"

    # A second poll with no pending rows makes no network calls.
    with factory() as session:
        assert dispatch_once(session, transport=transport).dispatched == 0
    assert len(calls) == 1


def test_worker_reconcile_once_finalizes_a_cancelling_run(engine, configure_settings) -> None:
    """M4-02 A2: the worker loop runs the reconciler each poll."""

    configure_settings(GITHUB_TOKEN="gh-token", EVAL_CANCEL_GRACE_SECONDS="0")
    factory: sessionmaker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.now(UTC)

    with factory() as session:
        ensure_default_scope(session)
        run, _ = evaluations.create_synthetic_evaluation(
            session,
            name="worker reconcile",
            case_keys=["w1"],
            idempotency_key="worker-reconcile",
            created_by="agent",
            settings=get_settings(),
        )
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
        attempt.status = "running"
        attempt.started_at = now
        attempt.phase_changed_at = now
        case.execution_status = "running"
        run.status = "running"
        run.github_run_id = "123"
        session.commit()
        evaluations.cancel_evaluation(session, run_id=run.id, now=now)
        assert session.get(EvalRun, run.id).status == "cancelling"

    with factory() as session:
        counts = reconcile_once(session, transport=lambda _url, **_kwargs: (202, ""))
    assert counts["cancelled"] == 1
    assert counts["heartbeat_timeout"] == 0

    with factory() as session:
        run = session.query(EvalRun).one()
        assert run.status == "cancelled"
        attempt = session.query(EvalCaseAttempt).one()
        assert attempt.status == "cancelled"
        assert attempt.error_code == "cancel_grace_exceeded"

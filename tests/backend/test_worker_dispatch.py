"""Worker loop consumes the shard-dispatch outbox each poll (M2-05)."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Outbox
from app.seed import ensure_default_scope
from app.services import evaluations
from app.worker.main import dispatch_once

from tests.backend.test_evaluations import configure_settings  # shared fixture


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

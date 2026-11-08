from __future__ import annotations

import pytest
from app.defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID, DEFAULT_WORKSPACE_ID
from app.models import (
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    Project,
    Tenant,
    Workspace,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _eval_run(session: Session, key: str = "idem-1", **overrides) -> EvalRun:
    values = {
        "tenant_id": str(DEFAULT_TENANT_ID),
        "project_id": str(DEFAULT_PROJECT_ID),
        "name": f"run-{key}",
        "idempotency_key": key,
        "created_by": "tester",
    }
    values.update(overrides)
    return EvalRun(**values)


def _case_run(session: Session, run: EvalRun, case_key: str, dataset_case_id: str | None = None) -> EvalCaseRun:
    return EvalCaseRun(
        tenant_id=run.tenant_id,
        eval_run_id=run.id,
        dataset_case_id=dataset_case_id or f"case-{case_key}",
        case_key=case_key,
    )


def test_duplicate_eval_run_idempotency_key_is_rejected(engine) -> None:
    with Session(engine) as session:
        session.add(_eval_run(session))
        session.commit()

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(_eval_run(session))
            session.commit()


def test_duplicate_case_run_keys_are_rejected(engine) -> None:
    with Session(engine) as session:
        run = _eval_run(session)
        session.add(run)
        session.commit()
        run_id = run.id

    with Session(engine) as session:
        run = session.get(EvalRun, run_id)
        session.add(_case_run(session, run, "case-a", "dataset-case-1"))
        session.commit()

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            run = session.get(EvalRun, run_id)
            session.add(_case_run(session, run, "case-a", "dataset-case-2"))
            session.commit()

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            run = session.get(EvalRun, run_id)
            session.add(_case_run(session, run, "case-b", "dataset-case-1"))
            session.commit()


def test_duplicate_attempt_number_is_rejected(engine) -> None:
    with Session(engine) as session:
        run = _eval_run(session)
        session.add(run)
        session.commit()
        case = _case_run(session, run, "case-attempt")
        session.add(case)
        session.commit()
        case_id = case.id

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            case = session.get(EvalCaseRun, case_id)
            session.add(EvalCaseAttempt(tenant_id=case.tenant_id, eval_case_run_id=case.id, attempt_no=1))
            session.add(EvalCaseAttempt(tenant_id=case.tenant_id, eval_case_run_id=case.id, attempt_no=1))
            session.commit()


def test_duplicate_dispatch_key_is_rejected(engine) -> None:
    with Session(engine) as session:
        run = _eval_run(session)
        session.add(run)
        session.commit()
        first = _case_run(session, run, "case-dispatch-1")
        second = _case_run(session, run, "case-dispatch-2")
        session.add_all([first, second])
        session.commit()
        first_id, second_id = first.id, second.id

    with Session(engine) as session:
        session.add(
            EvalCaseAttempt(
                tenant_id=str(DEFAULT_TENANT_ID),
                eval_case_run_id=first_id,
                attempt_no=1,
                dispatch_key="dispatch-key-1",
            )
        )
        session.commit()

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(
                EvalCaseAttempt(
                    tenant_id=str(DEFAULT_TENANT_ID),
                    eval_case_run_id=second_id,
                    attempt_no=1,
                    dispatch_key="dispatch-key-1",
                )
            )
            session.commit()


def test_duplicate_event_sequence_is_rejected(engine) -> None:
    with Session(engine) as session:
        run = _eval_run(session)
        session.add(run)
        session.commit()
        case = _case_run(session, run, "case-event")
        session.add(case)
        session.commit()
        run_id, case_id = run.id, case.id

    def event() -> EvalEvent:
        return EvalEvent(
            tenant_id=str(DEFAULT_TENANT_ID),
            eval_run_id=run_id,
            eval_case_run_id=case_id,
            attempt_no=1,
            sequence=1,
            event_type="eval.case.updated",
            payload_json={"ok": True},
        )

    with Session(engine) as session:
        session.add(event())
        session.commit()

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(event())
            session.commit()


def test_cross_tenant_composite_foreign_key_is_rejected(engine) -> None:
    other_tenant_id = "00000000-0000-0000-0000-0000000000aa"
    other_workspace_id = "00000000-0000-0000-0000-0000000000ab"

    with Session(engine) as session:
        session.add(Tenant(id=other_tenant_id, slug="other", name="Other", status="active"))
        session.flush()
        session.add(
            Workspace(
                id=other_workspace_id,
                tenant_id=other_tenant_id,
                slug="default",
                name="Other workspace",
            )
        )
        session.commit()

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(
                Project(
                    tenant_id=other_tenant_id,
                    workspace_id=str(DEFAULT_WORKSPACE_ID),
                    slug="bad-project",
                    name="Bad project",
                )
            )
            session.commit()

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(
                EvalRun(
                    tenant_id=other_tenant_id,
                    project_id=str(DEFAULT_PROJECT_ID),
                    name="bad-run",
                    idempotency_key="bad-run",
                    created_by="tester",
                )
            )
            session.commit()

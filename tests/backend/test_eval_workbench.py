"""M5-05 evaluation results workbench (issue #87).

Covers the read surface the detail page depends on (filtered cases, attempt
history, frozen config, newest-first event windows) and the retry recovery
actions, including their guard rails.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.defaults import DEFAULT_PROJECT_ID
from app.models import (
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    Outbox,
    ResourcePool,
    TestCaseResult,
)
from app.seed import ensure_default_scope
from app.services import evaluations
from dataset_helpers import fixture_records
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def _create_run(
    session: Session,
    *,
    case_keys: tuple[str, ...] = ("case-a", "case-b", "case-c"),
    idempotency_key: str = "workbench",
) -> EvalRun:
    run, _created = evaluations.create_synthetic_evaluation(
        session,
        name="工作台冒烟",
        case_keys=list(case_keys),
        shard_size=2,
        idempotency_key=idempotency_key,
        created_by="agent",
        settings=get_settings(),
    )
    session.commit()
    return run


def _cases(session: Session, run: EvalRun) -> list[EvalCaseRun]:
    return list(
        session.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )


def _attempt(session: Session, case: EvalCaseRun, attempt_no: int = 1) -> EvalCaseAttempt:
    attempt = session.scalar(
        select(EvalCaseAttempt).where(
            EvalCaseAttempt.eval_case_run_id == case.id,
            EvalCaseAttempt.attempt_no == attempt_no,
        )
    )
    assert attempt is not None
    return attempt


def _fail_case(session: Session, run: EvalRun, case: EvalCaseRun, *, status: str = "failed") -> None:
    attempt = _attempt(session, case)
    now = datetime.now(UTC)
    attempt.status = status
    attempt.ended_at = now
    attempt.error_code = "runtime_error"
    attempt.error_summary = "合成失败"
    case.execution_status = status
    case.error_code = "runtime_error"
    case.error_summary = "合成失败"
    case.result_status = "not_scored"
    run.status = "running"
    run.started_at = now
    session.commit()


def _add_test(
    session: Session,
    case: EvalCaseRun,
    *,
    group: str = "F2P",
    outcome: str = "failed",
    fail_message: str = "assert 1 == 2",
) -> TestCaseResult:
    attempt = _attempt(session, case)
    test = TestCaseResult(
        tenant_id=case.tenant_id,
        eval_case_attempt_id=attempt.id,
        suite_name="tests.test_workbench",
        test_name=f"test_{group.lower()}_{outcome}",
        outcome=outcome,
        native_status=outcome.upper(),
        test_group=group,
        expected=True,
        duration_ms=17,
        failure_message=fail_message,
        log_object_key="eval-logs/case/run.log",
    )
    session.add(test)
    session.commit()
    return test


def _prepare_failed_run(session: Session, *, key: str = "failed-run") -> tuple[EvalRun, list[EvalCaseRun]]:
    run = _create_run(session, idempotency_key=key)
    cases = _cases(session, run)
    _fail_case(session, run, cases[0], status="failed")
    _fail_case(session, run, cases[1], status="timeout")
    return run, cases


# ---------------------------------------------------------------------------
# Cases tab
# ---------------------------------------------------------------------------


def test_cases_filters_counts_and_test_summary(
    seeded: Session, client: TestClient
) -> None:
    run, cases = _prepare_failed_run(seeded)
    _add_test(seeded, cases[0], group="F2P", outcome="failed")
    _add_test(seeded, cases[0], group="P2P", outcome="passed")

    body = client.get(f"/api/v1/eval-runs/{run.id}/cases").json()
    assert body["total"] == 3
    assert body["counts"] == {
        "all": 3,
        "resolved": 0,
        "unresolved": 0,
        "running": 0,
        "failed": 2,
        "queued": 1,
    }
    assert [item["case_key"] for item in body["items"]] == ["case-a", "case-b", "case-c"]
    assert body["items"][0]["trace_status"] == "pending"
    assert body["items"][0]["test_summary"]["f2p_failed"] == 1
    assert body["items"][0]["test_summary"]["passed"] == 1

    failed = client.get(f"/api/v1/eval-runs/{run.id}/cases", params={"status": "failed"}).json()
    assert failed["total"] == 2
    assert {item["execution_status"] for item in failed["items"]} == {"failed", "timeout"}

    queued = client.get(f"/api/v1/eval-runs/{run.id}/cases", params={"status": "queued"}).json()
    assert queued["total"] == 1
    assert queued["items"][0]["case_key"] == "case-c"

    searched = client.get(f"/api/v1/eval-runs/{run.id}/cases", params={"q": "B"}).json()
    assert [item["case_key"] for item in searched["items"]] == ["case-b"]

    explicit = client.get(
        f"/api/v1/eval-runs/{run.id}/cases", params={"execution_status": "timeout"}
    ).json()
    assert explicit["total"] == 1

    paged = client.get(
        f"/api/v1/eval-runs/{run.id}/cases", params={"limit": 1, "offset": 1}
    ).json()
    assert paged["total"] == 3
    assert len(paged["items"]) == 1
    assert paged["items"][0]["case_key"] == "case-b"
    assert paged["page_info"] == {
        "limit": 1,
        "offset": 1,
        "total": 3,
        "has_more": True,
        "next_offset": 2,
    }


def test_cases_reject_invalid_filters(seeded: Session, client: TestClient) -> None:
    run = _create_run(seeded)
    assert (
        client.get(f"/api/v1/eval-runs/{run.id}/cases", params={"status": "bogus"}).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/v1/eval-runs/{run.id}/cases", params={"execution_status": "bogus"}
        ).status_code
        == 422
    )
    assert client.get("/api/v1/eval-runs/missing/cases").status_code == 404


def test_case_detail_lists_attempts_and_tests(seeded: Session, client: TestClient) -> None:
    run, cases = _prepare_failed_run(seeded, key="detail-run")
    _add_test(seeded, cases[0], group="F2P", outcome="failed")

    body = client.get(f"/api/v1/eval-case-runs/{cases[0].id}").json()
    assert body["case"]["id"] == cases[0].id
    assert body["case"]["error_code"] == "runtime_error"
    assert body["run"]["max_attempts"] == run.max_attempts
    assert len(body["attempts"]) == 1
    attempt = body["attempts"][0]
    assert attempt["attempt_no"] == 1
    assert attempt["status"] == "failed"
    assert attempt["test_summary"]["f2p_failed"] == 1
    assert body["retry"] == {"retryable": True, "reason": None}
    assert body["tests_attempt"] == 1

    assert client.get("/api/v1/eval-case-runs/missing").status_code == 404


# ---------------------------------------------------------------------------
# Retry actions
# ---------------------------------------------------------------------------


def test_retry_case_creates_attempt_outbox_and_event(
    seeded: Session, client: TestClient
) -> None:
    run, cases = _prepare_failed_run(seeded, key="retry-run")
    target = cases[0]
    outbox_before = len(seeded.scalars(select(Outbox)).all())

    response = client.post(f"/api/v1/eval-case-runs/{target.id}/retry")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["case"]["execution_status"] == "queued"
    assert body["attempt"]["attempt_no"] == 2
    assert body["attempt"]["status"] == "pending"

    seeded.expire_all()
    refreshed = seeded.get(EvalCaseRun, target.id)
    assert refreshed is not None
    assert refreshed.execution_status == "queued"
    assert refreshed.latest_attempt_no == 2

    attempts = list(
        seeded.scalars(
            select(EvalCaseAttempt)
            .where(EvalCaseAttempt.eval_case_run_id == target.id)
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )
    assert [attempt.attempt_no for attempt in attempts] == [1, 2]
    assert attempts[1].retry_reason == "manual_retry"
    assert len(seeded.scalars(select(Outbox)).all()) == outbox_before + 1

    event = seeded.scalar(
        select(EvalEvent).where(EvalEvent.event_type == "case.retry_requested")
    )
    assert event is not None
    assert event.eval_case_run_id == target.id
    assert event.attempt_no == 2
    assert event.payload_json["requested_by"] == "admin@test.local"

    # The exact same request cannot double-retry: the case already moved on.
    again = client.post(f"/api/v1/eval-case-runs/{target.id}/retry")
    assert again.status_code == 409
    assert again.json()["detail"]["error"]["code"] == "case_retry_case_not_failed"

    # A terminal run is no longer a valid retry target.
    run.status = "completed"
    other = seeded.get(EvalCaseRun, cases[1].id)
    assert other is not None
    other.execution_status = "failed"
    seeded.commit()
    rejected = client.post(f"/api/v1/eval-case-runs/{other.id}/retry")
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error"]["code"] == "case_retry_run_not_running"


def test_retry_failed_cases_is_bulk_and_traced(
    seeded: Session, client: TestClient
) -> None:
    run, cases = _prepare_failed_run(seeded, key="bulk-run")
    outbox_before = len(seeded.scalars(select(Outbox)).all())

    response = client.post(f"/api/v1/eval-runs/{run.id}/retry-failed")
    assert response.status_code == 200, response.text
    assert response.json()["retried"] == 2
    assert response.json()["skipped"] == 0

    seeded.expire_all()
    for case in cases[:2]:
        refreshed = seeded.get(EvalCaseRun, case.id)
        assert refreshed is not None
        assert refreshed.execution_status == "queued"
        assert refreshed.latest_attempt_no == 2
    assert len(seeded.scalars(select(Outbox)).all()) == outbox_before + 2

    event = seeded.scalar(select(EvalEvent).where(EvalEvent.event_type == "eval.retry_failed"))
    assert event is not None
    assert event.payload_json["retried"] == 2

    no_more = client.post(f"/api/v1/eval-runs/{run.id}/retry-failed")
    assert no_more.status_code == 200
    assert no_more.json() == {"status": "ok", "run_id": run.id, "retried": 0, "skipped": 0}


def test_retry_requires_admin_session(seeded: Session, anonymous_client: TestClient) -> None:
    run, cases = _prepare_failed_run(seeded, key="auth-run")
    response = anonymous_client.post(f"/api/v1/eval-case-runs/{cases[0].id}/retry")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Config snapshot
# ---------------------------------------------------------------------------


def test_config_snapshot_explains_the_frozen_run(
    seeded: Session, client: TestClient
) -> None:
    import_payload = {
        "dataset_slug": "swebench-verified",
        "dataset_name": "SWE-bench Verified",
        "adapter_key": "swebench",
        "version": "workbench-config",
        "source_uri": "tests/backend/fixtures/swebench/verified-slice.jsonl",
        "source_revision": "rev-workbench",
        "records": fixture_records(),
    }
    imported = client.post("/api/v1/dataset-imports", json=import_payload)
    assert imported.status_code in (200, 201), imported.text
    version = imported.json()["version"]

    model = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "openai",
            "display_name": "工作台模型",
            "base_url": "https://provider.test/v1",
            "model_name": "gpt-workbench",
            "secret": "sk-workbench-123456",
        },
    )
    assert model.status_code == 201, model.text
    model_id = model.json()["id"]

    run, _created = evaluations.create_synthetic_evaluation(
        seeded,
        name="",
        case_keys=[],
        dataset_version_id=version["id"],
        model_config_id=model_id,
        runtime_key="claude-code",
        runtime_version="2.1.0",
        idempotency_key="config-run",
        created_by="agent",
        execution_image_digest="sha256:exec",
        scoring_image_digest="sha256:score",
        asset_snapshot_json={
            "snapshot_id": "snap-1",
            "source_id": "src-1",
            "requested_ref": "main",
            "resolved_commit": "abc123",
            "asset_count": 2,
            "file_count": 4,
            "assets": [
                {"asset_type": "skill", "name": "brainstorming", "path": "skills/brainstorming"},
                {"asset_type": "command", "name": "test", "path": "commands/test.md"},
            ],
        },
        consistency_json={
            "effective_concurrency": 4,
            "blockers": [],
            "warnings": [{"code": "capacity_queue_expected", "message": "容量不足"}],
        },
        settings=get_settings(),
    )
    pool = ResourcePool(
        tenant_id=run.tenant_id,
        project_id=str(DEFAULT_PROJECT_ID),
        name="linux-pool",
        provider_key="github",
        provisioning_mode="fixed",
        max_capacity=8,
    )
    seeded.add(pool)
    seeded.flush()
    run.resource_pool_id = pool.id
    seeded.commit()

    body = client.get(f"/api/v1/eval-runs/{run.id}/config").json()
    assert body["dataset"]["version"] == "workbench-config"
    assert body["dataset"]["dataset_name"] == "SWE-bench Verified"
    assert body["dataset"]["case_count"] == len(fixture_records())
    assert body["runtime"]["key"] == "claude-code"
    assert body["model"]["model_name"] == "gpt-workbench"
    assert body["resource_pool"]["name"] == "linux-pool"
    assert body["resource_pool"]["max_capacity"] == 8
    assert body["execution"]["case_timeout_seconds"] > 0
    assert body["execution"]["max_attempts"] == 2
    assert body["execution"]["shard_size"] == run.shard_size
    assert body["execution"]["execution_image_digest"] == "sha256:exec"
    assert body["consistency"]["effective_concurrency"] == 4
    assert body["consistency"]["warnings"][0]["code"] == "capacity_queue_expected"
    assert body["assets"]["asset_count"] == 2
    assert body["assets"]["items"][0]["name"] == "brainstorming"
    assert body["metrics"], "the dataset version must expose metric definitions"
    assert body["metrics"][0]["display_kind"]

    serialized = str(body)
    assert "sk-workbench" not in serialized
    assert "ciphertext" not in serialized
    assert client.get("/api/v1/eval-runs/missing/config").status_code == 404


# ---------------------------------------------------------------------------
# Events + detail counts
# ---------------------------------------------------------------------------


def test_events_open_at_the_newest_window_and_page_backwards(
    seeded: Session, client: TestClient
) -> None:
    run = _create_run(seeded, idempotency_key="events-run")
    for sequence in range(1, 4):
        seeded.add(
            EvalEvent(
                tenant_id=run.tenant_id,
                eval_run_id=run.id,
                sequence=sequence,
                event_type="eval.note",
                payload_json={"n": sequence},
            )
        )
    seeded.commit()

    newest = client.get(f"/api/v1/evaluations/{run.id}/events", params={"limit": 2}).json()
    assert [item["sequence"] for item in newest["items"]] == [2, 3]
    assert newest["has_more"] is True
    assert newest["direction"] == "older"
    assert newest["next_cursor"] == newest["items"][0]["id"]

    older = client.get(
        f"/api/v1/evaluations/{run.id}/events",
        params={"before_id": newest["next_cursor"], "limit": 2},
    ).json()
    assert [item["sequence"] for item in older["items"]] == [0, 1]
    assert older["has_more"] is False
    assert older["next_cursor"] is None

    forward = client.get(
        f"/api/v1/evaluations/{run.id}/events",
        params={"after_id": older["items"][-1]["id"], "limit": 2},
    ).json()
    assert [item["sequence"] for item in forward["items"]] == [2, 3]
    assert forward["direction"] == "newer"
    assert forward["has_more"] is False


def test_detail_exposes_case_and_trace_counts(seeded: Session, client: TestClient) -> None:
    run, cases = _prepare_failed_run(seeded, key="counts-run")
    cases[0].trace_status = "ready"
    seeded.commit()

    body = client.get(f"/api/v1/evaluations/{run.id}").json()
    assert body["case_counts"] == {
        "all": 3,
        "resolved": 0,
        "unresolved": 0,
        "running": 0,
        "failed": 2,
        "queued": 1,
    }
    assert body["trace_counts"] == {"pending": 2, "ready": 1}
    assert body["retryable_count"] == 2
    assert body["cases"][0]["trace_status"] == "ready"


def test_detail_progress_snapshot_is_aggregated_server_side(seeded: Session) -> None:
    """Percent, resolve rate and ETA come from one server snapshot (M5-17)."""

    run = _create_run(
        seeded,
        case_keys=("case-a", "case-b", "case-c", "case-d"),
        idempotency_key="progress-run",
    )
    cases = _cases(seeded, run)
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    run.status = "running"
    run.started_at = now - timedelta(seconds=600)
    run.queued_count = 0
    run.running_count = 1
    run.succeeded_count = 2
    run.failed_count = 1
    statuses = [
        ("succeeded", "resolved"),
        ("succeeded", "resolved"),
        ("failed", "not_scored"),
        ("running", "not_scored"),
    ]
    for case, (execution, result) in zip(cases, statuses, strict=True):
        case.execution_status = execution
        case.result_status = result
    seeded.commit()

    progress = evaluations.evaluation_detail(seeded, run, now=now)["progress"]
    assert progress == {
        "total": 4,
        "finished": 3,
        "percent": 75,
        "resolve_rate": 66.7,
        "estimated_remaining_seconds": 200,
        "estimated_finish_at": "2026-11-11T12:03:20.000000+00:00",
    }

    # A paused budget run keeps its progress but must not advertise an ETA.
    run.status = "paused_budget"
    run.pause_reason = "budget_exceeded"
    seeded.commit()
    paused = evaluations.evaluation_detail(seeded, run, now=now)["progress"]
    assert paused["percent"] == 75
    assert paused["estimated_remaining_seconds"] is None
    assert paused["estimated_finish_at"] is None

    # A terminal run returns a final snapshot without a moving ETA.
    run.pause_reason = None
    for case in cases:
        case.execution_status = "succeeded"
        case.result_status = "resolved"
    run.status = "completed"
    run.queued_count = 0
    run.running_count = 0
    seeded.commit()
    final = evaluations.evaluation_detail(seeded, run, now=now)["progress"]
    assert final["percent"] == 100
    assert final["resolve_rate"] == 100.0
    assert final["estimated_remaining_seconds"] is None
    assert final["estimated_finish_at"] is None


def test_retry_conflict_maps_to_409(seeded: Session, client: TestClient) -> None:
    """Two tabs retrying the same case must not surface a 500."""

    _run, cases = _prepare_failed_run(seeded, key="conflict-run")
    target = cases[0]
    # Simulate the winner of the race: attempt 2 already exists, while the
    # losing request still sees the failed attempt 1 as the latest.
    seeded.add(
        EvalCaseAttempt(
            tenant_id=target.tenant_id,
            eval_case_run_id=target.id,
            attempt_no=2,
            status="pending",
            dispatch_key=f"conflict-{target.id}",
            shard_id="shard-conflict",
        )
    )
    seeded.commit()

    response = client.post(f"/api/v1/eval-case-runs/{target.id}/retry")
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "case_retry_conflict"

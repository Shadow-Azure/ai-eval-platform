"""M4-02 A2: cancellation reconciler and lost-callback completion backfill.

验收对照：

- `cancelling` 的 run 必须找到 GitHub workflow run 并只 POST 一次 cancel（事件
  去重，注入 transport，不触网）；GitHub 失败要留下 `eval.cancel_failed` 并在
  下一轮重试；
- 宽限期内的 / 已完成的 attempt 不能被重复收口；
- 丢失的 completed 回调通过对象存储校验后用同一 completion core 回填，写
  `case.reconciled_completed`，绝不新建 attempt；
- 健康 run（非 cancelling）不产生任何 GitHub 调用。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun
from app.seed import ensure_default_scope
from app.services import evaluations, reconciler
from object_stub import StubObjectStore
from sqlalchemy import func, select
from sqlalchemy.orm import Session

COUNT_KEYS = {
    "queue_timeout",
    "execution_timeout",
    "scoring_timeout",
    "upload_timeout",
    "heartbeat_timeout",
    "cancelled",
    "reconciled",
}


class FakeGitHub:
    """Injected GitHub transport: workflow-run lookup + run cancel."""

    def __init__(self, workflow_runs: list[dict] | None = None, *, cancel_status: int = 202) -> None:
        self.workflow_runs = list(workflow_runs or [])
        self.cancel_status = cancel_status
        self.fail_cancel = False
        self.calls: list[dict] = []

    @property
    def cancel_calls(self) -> list[dict]:
        return [call for call in self.calls if call["url"].endswith("/cancel")]

    def __call__(self, url: str, *, method: str, body, headers, timeout: float):
        self.calls.append(
            {"url": url, "method": method, "body": body, "headers": headers, "timeout": timeout}
        )
        if "/actions/runs?" in url:
            return 200, json.dumps({"workflow_runs": self.workflow_runs})
        if url.endswith("/cancel"):
            if self.fail_cancel:
                return 500, '{"message":"server error"}'
            return self.cancel_status, ""
        raise AssertionError(f"unexpected GitHub URL: {url}")


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_run(
    session: Session,
    *,
    key: str,
    case_keys: tuple[str, ...] = ("case-a", "case-b"),
) -> EvalRun:
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="reconciler 冒烟",
        case_keys=list(case_keys),
        idempotency_key=key,
        created_by="agent",
        settings=get_settings(),
    )
    return run


def _case(session: Session, run: EvalRun, case_key: str = "case-a") -> EvalCaseRun:
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


def _make_running(session: Session, run: EvalRun, case: EvalCaseRun, *, now: datetime) -> EvalCaseAttempt:
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


def _attempt_count(session: Session, run: EvalRun) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(EvalCaseRun.eval_run_id == run.id)
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Cancellation reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_finds_and_cancels_the_workflow_run_once(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    now = datetime.now(UTC)
    run = _create_run(seeded, key="reconcile-find", case_keys=("case-a",))
    case = _case(seeded, run)
    _make_running(seeded, run, case, now=now)
    evaluations.cancel_evaluation(seeded, run_id=run.id, now=now)

    transport = FakeGitHub(
        [
            {
                "id": 424242,
                "display_title": f"eval-shard {run.id}:shard_000:1",
                "name": "eval-shard",
            }
        ]
    )
    counts = reconciler.reconcile(
        seeded, settings=get_settings(), now=now + timedelta(seconds=5), transport=transport
    )

    assert set(counts) == COUNT_KEYS
    assert counts["cancelled"] == 0  # the running attempt still settles
    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.status == "cancelling"
    assert run.github_run_id == "424242"

    lookup = transport.calls[0]
    assert lookup["method"] == "GET"
    assert "/repos/" in lookup["url"]
    assert "event=repository_dispatch" in lookup["url"]
    assert len(transport.cancel_calls) == 1
    assert transport.cancel_calls[0]["method"] == "POST"
    requested = _run_events(seeded, run.id, "eval.cancel_requested")
    assert len(requested) == 1
    assert requested[0].payload_json["github_run_id"] == "424242"

    # Second pass: no duplicate lookup (id already stored) and no second cancel.
    calls_after_first = len(transport.calls)
    second = reconciler.reconcile(
        seeded, settings=get_settings(), now=now + timedelta(seconds=6), transport=transport
    )
    assert second["cancelled"] == 0
    assert len(transport.cancel_calls) == 1
    assert len(_run_events(seeded, run.id, "eval.cancel_requested")) == 1
    assert len(transport.calls) == calls_after_first


def test_cancel_failure_is_recorded_and_retried(seeded: Session, configure_settings) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    now = datetime.now(UTC)
    run = _create_run(seeded, key="reconcile-fail", case_keys=("case-a",))
    case = _case(seeded, run)
    _make_running(seeded, run, case, now=now)
    run.github_run_id = "777"
    seeded.commit()
    evaluations.cancel_evaluation(seeded, run_id=run.id, now=now)

    transport = FakeGitHub()
    transport.fail_cancel = True
    reconciler.reconcile(
        seeded, settings=get_settings(), now=now + timedelta(seconds=5), transport=transport
    )
    assert len(transport.cancel_calls) == 1
    failed = _run_events(seeded, run.id, "eval.cancel_failed")
    assert len(failed) == 1
    assert failed[0].payload_json["github_run_id"] == "777"
    assert "HTTP 500" in failed[0].payload_json["error"]
    assert _run_events(seeded, run.id, "eval.cancel_requested") == []

    # Next cycle retries and succeeds; the failure event stays as audit.
    transport.fail_cancel = False
    reconciler.reconcile(
        seeded, settings=get_settings(), now=now + timedelta(seconds=10), transport=transport
    )
    assert len(transport.cancel_calls) == 2
    requested = _run_events(seeded, run.id, "eval.cancel_requested")
    assert len(requested) == 1
    assert requested[0].payload_json["github_run_id"] == "777"


def test_reconcile_does_not_call_github_for_healthy_runs(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    run = _create_run(seeded, key="reconcile-healthy")
    transport = FakeGitHub()

    counts = reconciler.reconcile(seeded, settings=get_settings(), transport=transport)

    assert transport.calls == []
    assert counts["cancelled"] == 0
    assert counts["reconciled"] == 0
    seeded.expire_all()
    assert seeded.get(EvalRun, run.id).status == "queued"


def test_reconcile_finalizes_when_no_active_attempt_remains(
    seeded: Session, configure_settings
) -> None:
    configure_settings(GITHUB_TOKEN="gh-token")
    now = datetime.now(UTC)
    run = _create_run(seeded, key="reconcile-final", case_keys=("case-a", "case-b"))
    case_a = _case(seeded, run, "case-a")
    _make_running(seeded, run, case_a, now=now)
    run.github_run_id = "99"
    seeded.commit()
    evaluations.cancel_evaluation(seeded, run_id=run.id, now=now)

    # The started attempt fails (stale callback) → no active attempt remains.
    _attempt(seeded, case_a).status = "failed"
    case_a.execution_status = "failed"
    seeded.commit()
    transport = FakeGitHub()

    counts = reconciler.reconcile(
        seeded, settings=get_settings(), now=now + timedelta(seconds=5), transport=transport
    )
    assert counts["cancelled"] == 1
    seeded.expire_all()
    run = seeded.get(EvalRun, run.id)
    assert run.status == "cancelled"
    assert run.finished_at is not None
    assert len(_run_events(seeded, run.id, "eval.cancelled")) == 1
    # A terminal case is not regressed by the run-level cancellation.
    assert seeded.get(EvalCaseRun, case_a.id).execution_status == "failed"


# ---------------------------------------------------------------------------
# Completion backfill (lost callback)
# ---------------------------------------------------------------------------


def _uploading_attempt(
    session: Session,
    run: EvalRun,
    case: EvalCaseRun,
    *,
    now: datetime,
    objects: list[dict],
) -> EvalCaseAttempt:
    attempt = _attempt(session, case)
    attempt.status = "uploading"
    attempt.started_at = now - timedelta(seconds=42)
    attempt.phase_changed_at = now - timedelta(seconds=5)
    attempt.artifact_manifest_json = {"objects": objects}
    case.execution_status = "running"
    run.status = "running"
    if run.started_at is None:
        run.started_at = now
    session.commit()
    return attempt


def test_completion_backfill_finalizes_uploading_attempt(
    seeded: Session, configure_settings
) -> None:
    report = {"case-a": {"resolved": True, "tests_status": {}}}
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        now = datetime.now(UTC)
        run = _create_run(seeded, key="backfill", case_keys=("case-a",))
        case = _case(seeded, run)
        score_key = store.add_bytes(f"evaluations/{run.id}/score.json", json.dumps(report).encode())
        trace_key = store.add(f"evaluations/{run.id}/trace.ndjson", 120)
        attempt = _uploading_attempt(
            seeded,
            run,
            case,
            now=now,
            objects=[
                {
                    "artifact_kind": "score",
                    "object_key": score_key,
                    "sha256": "a" * 64,
                    "size": len(json.dumps(report).encode()),
                    "content_type": "application/json",
                },
                {
                    "artifact_kind": "trace",
                    "object_key": trace_key,
                    "sha256": "b" * 64,
                    "size": 120,
                    "content_type": "application/x-ndjson",
                },
            ],
        )
        attempts_before = _attempt_count(seeded, run)

        counts = reconciler.reconcile(seeded, settings=get_settings(), now=now)

        assert counts["reconciled"] == 1
        seeded.expire_all()
        attempt = seeded.get(EvalCaseAttempt, attempt.id)
        case = seeded.get(EvalCaseRun, case.id)
        run = seeded.get(EvalRun, run.id)
        assert attempt.status == "succeeded"
        assert attempt.error_code is None
        assert case.execution_status == "succeeded"
        assert case.result_status == "resolved"
        assert case.trace_status == "ready"
        assert run.status == "completed"
        assert run.succeeded_count == 1
        # No new attempt and no runner-reported completion event.
        assert _attempt_count(seeded, run) == attempts_before
        assert len(_case_events(seeded, case.id, "case.reconciled_completed")) == 1
        assert _case_events(seeded, case.id, "case.completed") == []
        payload = _case_events(seeded, case.id, "case.reconciled_completed")[0].payload_json
        assert payload["source"] == "reconciler"
        assert payload["attempt_id"] == attempt.id
        assert payload["result_status"] == "resolved"

        # Replay: nothing left in uploading, no duplicate event/attempt.
        events_before = seeded.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).count()
        again = reconciler.reconcile(seeded, settings=get_settings(), now=now + timedelta(seconds=5))
        assert again["reconciled"] == 0
        assert seeded.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).count() == events_before
        assert _attempt_count(seeded, run) == attempts_before


def test_completion_backfill_requires_the_score_object(
    seeded: Session, configure_settings
) -> None:
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        now = datetime.now(UTC)
        run = _create_run(seeded, key="backfill-missing", case_keys=("case-a",))
        case = _case(seeded, run)
        # Manifest claims an object the store does not have: the reaper owns the
        # attempt, the reconciler must not invent a success.
        attempt = _uploading_attempt(
            seeded,
            run,
            case,
            now=now,
            objects=[
                {
                    "artifact_kind": "score",
                    "object_key": f"evaluations/{run.id}/ghost.json",
                    "sha256": "c" * 64,
                    "size": 10,
                    "content_type": "application/json",
                }
            ],
        )
        counts = reconciler.reconcile(seeded, settings=get_settings(), now=now)

    assert counts["reconciled"] == 0
    seeded.expire_all()
    assert seeded.get(EvalCaseAttempt, attempt.id).status == "uploading"
    assert _case_events(seeded, case.id, "case.reconciled_completed") == []

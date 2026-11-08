"""M2-05 acceptance core: started/heartbeat/uploads/completed/failed callbacks.

验收 3 的钉子：
- 重复回调（同 event_id / 同 sequence）→ 幂等去重，不重复推进、不重复计数。
- 乱序回调（低 sequence 心跳、终态后的心跳/started、旧 attempt）→ 409 保持
  状态不回退。
- 对象缺失 → attempt artifact_missing + case failed，绝不标完整成功。
- Runner 失败（不可重试）→ case/run 失败；可重试且未达上限 → 新 attempt +
  新 dispatch（旧 attempt 保持 failed，不产生重复 attempt）。
- 平台短期 token（M2-04 遗留 b）：配置 RUNNER_TASK_SECRET 后回调必须携带
  与 attempt 绑定的平台签发 token。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.config import get_settings
from app.models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox
from app.seed import ensure_default_scope
from app.services import evaluations
from app.services.short_token import issue_short_token
from case_helpers import (
    SECRET,
    auth,
    case_url,
    completion_body,
    failure_body,
    heartbeat_body,
    started_body,
    upload_body,
)
from fastapi.testclient import TestClient
from object_stub import StubObjectStore
from sqlalchemy.orm import Session


def attempt_rows(env: dict, case_run_id: str) -> list[EvalCaseAttempt]:
    env["session"].expire_all()  # callbacks commit in the app's session
    return (
        env["session"]
        .query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case_run_id)
        .order_by(EvalCaseAttempt.attempt_no)
        .all()
    )


def run_row(env: dict) -> EvalRun:
    env["session"].expire_all()
    return env["session"].query(EvalRun).filter(EvalRun.id == env["run_id"]).one()


# ---------------------------------------------------------------------------
# Started / heartbeat
# ---------------------------------------------------------------------------


def test_started_moves_attempt_and_case_to_running(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    response = client.post(
        case_url(env, case_run_id, "started"),
        json=started_body(env, case_run_id),
        headers=auth(env),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"

    attempt = attempt_rows(env, case_run_id)[0]
    assert attempt.status == "running"
    assert attempt.started_at is not None
    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.execution_status == "running"
    assert run_row(env).status == "running"

    # duplicate started (same event) is idempotent, no regression
    again = client.post(case_url(env, case_run_id, "started"), json=started_body(env, case_run_id), headers=auth(env))
    assert again.status_code == 200
    assert again.json()["deduped"] is True
    assert len(attempt_rows(env, case_run_id)) == 1


def test_started_for_unknown_case_is_404(eval_env, client: TestClient) -> None:
    env = eval_env
    response = client.post(
        case_url(env, "ecr_missing", "started"), json=started_body(env, "ecr_missing"), headers=auth(env)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "case_run_not_found"


def test_heartbeat_updates_sequence_and_rejects_reordering(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]

    first = client.post(case_url(env, case_run_id, "heartbeat"), json=heartbeat_body(case_run_id, 1), headers=auth(env))
    assert first.status_code == 200
    attempt = attempt_rows(env, case_run_id)[0]
    assert attempt.sequence == 1
    assert attempt.status == "running"  # heartbeat on queued attempt promotes

    second = client.post(
        case_url(env, case_run_id, "heartbeat"),
        json=heartbeat_body(case_run_id, 2),
        headers=auth(env),
    )
    assert second.status_code == 200

    duplicate = client.post(
        case_url(env, case_run_id, "heartbeat"),
        json=heartbeat_body(case_run_id, 2),
        headers=auth(env),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["deduped"] is True

    stale = client.post(case_url(env, case_run_id, "heartbeat"), json=heartbeat_body(case_run_id, 1), headers=auth(env))
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"]["code"] == "stale_sequence"

    attempt = attempt_rows(env, case_run_id)[0]
    assert attempt.sequence == 2  # no regression


def test_future_attempt_is_stale(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    response = client.post(
        case_url(env, case_run_id, "heartbeat"),
        json=heartbeat_body(case_run_id, 1, attempt=2),
        headers=auth(env, attempt=2),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "stale_attempt"


# ---------------------------------------------------------------------------
# Uploads + completion with object verification
# ---------------------------------------------------------------------------


def test_uploads_and_completion_close_the_case(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]

    uploads = client.post(case_url(env, case_run_id, "uploads"), json=upload_body(env, case_run_id), headers=auth(env))
    assert uploads.status_code == 200

    completed = client.post(
        case_url(env, case_run_id, "completed"), json=completion_body(env, case_run_id), headers=auth(env)
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"

    attempt = attempt_rows(env, case_run_id)[0]
    assert attempt.status == "succeeded"
    assert attempt.ended_at is not None
    objects = attempt.artifact_manifest_json["objects"]
    assert len(objects) == 1

    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.execution_status == "succeeded"
    assert case.result_status == "resolved"
    assert case.duration_ms == 1200


def test_duplicate_completion_is_idempotent(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    body = completion_body(env, case_run_id)
    first = client.post(case_url(env, case_run_id, "completed"), json=body, headers=auth(env))
    assert first.status_code == 200
    run_before = run_row(env)
    succeeded_before = run_before.succeeded_count

    second = client.post(case_url(env, case_run_id, "completed"), json=body, headers=auth(env))
    assert second.status_code == 200
    assert second.json()["deduped"] is True
    run_after = run_row(env)
    assert run_after.succeeded_count == succeeded_before
    assert run_after.status == "queued"  # case-b still queued; run not final

    events = (
        env["session"]
        .query(EvalEvent)
        .filter(EvalEvent.eval_case_run_id == case_run_id, EvalEvent.event_type == "case.completed")
        .all()
    )
    assert len(events) == 1


def test_completion_with_missing_object_marks_artifact_missing(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    missing = {"object_key": "evaluations/ghost/objects/deadbeef", "sha256": "0" * 64, "size": 10}
    body = completion_body(env, case_run_id, score=missing)

    response = client.post(case_url(env, case_run_id, "completed"), json=body, headers=auth(env))
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "artifact_missing"

    attempt = attempt_rows(env, case_run_id)[0]
    assert attempt.status == "artifact_missing"
    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.execution_status == "failed"
    assert case.error_code == "artifact_missing"
    run = run_row(env)
    assert run.failed_count == 1  # diagnosable at case/attempt level, not silently queued
    assert run.status == "queued"  # case-b never dispatched in this scenario → run not final

    # Terminal: a retry with a valid object cannot resurrect the attempt.
    late = client.post(
        case_url(env, case_run_id, "completed"), json=completion_body(env, case_run_id), headers=auth(env)
    )
    assert late.status_code == 409
    assert attempt_rows(env, case_run_id)[0].status == "artifact_missing"


def test_completed_after_cancel_does_not_regress(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    evaluations.cancel_evaluation(env["session"], run_id=env["run_id"])

    response = client.post(
        case_url(env, case_run_id, "completed"), json=completion_body(env, case_run_id), headers=auth(env)
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "stale_attempt"
    assert run_row(env).status == "cancelled"
    assert attempt_rows(env, case_run_id)[0].status == "cancelled"


# ---------------------------------------------------------------------------
# Runner failure + retryable new attempt
# ---------------------------------------------------------------------------


def test_non_retryable_runner_failure_fails_case_and_run(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    client.post(case_url(env, case_run_id, "started"), json=started_body(env, case_run_id), headers=auth(env))

    response = client.post(
        case_url(env, case_run_id, "failed"), json=failure_body(case_run_id), headers=auth(env)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"

    assert len(attempt_rows(env, case_run_id)) == 1  # no duplicate attempt
    attempt = attempt_rows(env, case_run_id)[0]
    assert attempt.status == "failed"
    assert attempt.error_code == "runtime_error"
    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.execution_status == "failed"
    run = run_row(env)
    assert run.status == "running"  # case-b not closed yet

    # close case-b successfully → mixed terminal outcome is partial
    case_b = env["cases"]["case-b"]
    ok = client.post(
        case_url(env, case_b, "completed"), json=completion_body(env, case_b), headers=auth(env)
    )
    assert ok.status_code == 200, ok.text
    run = run_row(env)
    assert run.status == "partial"
    assert run.succeeded_count == 1 and run.failed_count == 1


def test_retryable_failure_creates_exactly_one_new_attempt_and_dispatch(
    eval_env, client: TestClient
) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    client.post(case_url(env, case_run_id, "started"), json=started_body(env, case_run_id), headers=auth(env))
    body = failure_body(case_run_id, retryable=True, error_code="callback_retryable")

    first = client.post(case_url(env, case_run_id, "failed"), json=body, headers=auth(env))
    assert first.status_code == 200
    assert first.json()["retry_queued"] is True
    assert first.json()["status"] == "failed"  # attempt itself is terminal

    rows = attempt_rows(env, case_run_id)
    assert [r.attempt_no for r in rows] == [1, 2]
    assert rows[0].status == "failed"  # old attempt preserved for diagnosis
    assert rows[1].status == "pending"
    assert rows[1].dispatch_key != rows[0].dispatch_key

    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.execution_status == "queued"
    assert case.latest_attempt_no == 2

    # Exactly one new outbox row with a contract-valid payload for attempt 2.
    from app.runner_contracts import validate_payload

    retry_rows = env["session"].query(Outbox).all()
    retry = [r for r in retry_rows if r.payload_json.get("attempt") == 2]
    assert len(retry) == 1
    validate_payload("dispatch", retry[0].payload_json["client_payload"])
    assert retry[0].payload_json["client_payload"]["shard_id"].startswith(env["shard"])

    # duplicate retry callback (same event_id) must not create a third attempt
    duplicate = client.post(case_url(env, case_run_id, "failed"), json=body, headers=auth(env))
    assert duplicate.status_code == 200
    assert duplicate.json()["deduped"] is True
    assert [r.attempt_no for r in attempt_rows(env, case_run_id)] == [1, 2]


def test_retryable_failure_at_max_attempts_is_terminal(
    session: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(RUNNER_TASK_SECRET=SECRET)
    ensure_default_scope(session)
    with StubObjectStore() as store:
        configure_settings(S3_ENDPOINT=store.endpoint, S3_ACCESS_KEY="ak", S3_SECRET_KEY="sk")
        run, _ = evaluations.create_synthetic_evaluation(
            session,
            name="max attempts",
            case_keys=["only"],
            idempotency_key="max-att",
            created_by="agent",
            settings=get_settings(),
        )
        case_run = session.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id).one()
        assert run.max_attempts == 2

        def auth_for(attempt: int) -> dict:
            # Production shape: the runner fetches the retry manifest and gets a
            # token bound to the RETRY shard, not the original one.
            retry_shard = _shard_of(session, case_run.id) if attempt == 1 else (
                session.query(EvalCaseAttempt)
                .filter(EvalCaseAttempt.eval_case_run_id == case_run.id)
                .filter(EvalCaseAttempt.attempt_no == attempt)
                .one()
                .shard_id
            )
            token = issue_short_token(
                {"eval_run_id": run.id, "shard_id": retry_shard, "attempt": attempt},
                SECRET,
                ttl_seconds=600,
            )
            return {"Authorization": f"Bearer {token}"}

        first = client.post(
            f"/runner/v1/case-runs/{case_run.id}/failed",
            json=failure_body(case_run.id, attempt=1, retryable=True),
            headers=auth_for(1),
        )
        assert first.status_code == 200
        assert first.json()["retry_queued"] is True
        assert first.json()["status"] == "failed"

        second = client.post(
            f"/runner/v1/case-runs/{case_run.id}/failed",
            json=failure_body(case_run.id, attempt=2, retryable=True, event_id="evt_only_failed_2"),
            headers=auth_for(2),
        )
        assert second.status_code == 200
        assert second.json()["retry_queued"] is False  # max_attempts exhausted
        assert second.json()["status"] == "failed"

        attempt_numbers = [
            a.attempt_no
            for a in session.query(EvalCaseAttempt)
            .filter(EvalCaseAttempt.eval_case_run_id == case_run.id)
            .all()
        ]
        assert attempt_numbers == [1, 2]  # no attempt 3
        session.expire_all()  # callbacks committed in the app session
        run_row = session.query(EvalRun).filter(EvalRun.id == run.id).one()
        assert run_row.status == "failed"


def _shard_of(session: Session, case_run_id: str) -> str:
    return (
        session.query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case_run_id)
        .one()
        .shard_id
    )


def _build_app():
    from app.main import app

    return app


# ---------------------------------------------------------------------------
# Short-term token enforcement (M2-04 leftover b)
# ---------------------------------------------------------------------------


def test_callbacks_require_platform_token_when_configured(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    body = heartbeat_body(case_run_id, 1)

    missing = client.post(case_url(env, case_run_id, "heartbeat"), json=body)
    assert missing.status_code == 401
    assert missing.json()["detail"]["error"]["code"] == "runner_token_invalid"

    wrong = client.post(
        case_url(env, case_run_id, "heartbeat"), json=body, headers={"Authorization": "Bearer v1.bad.bad"}
    )
    assert wrong.status_code == 401

    other_attempt = client.post(
        case_url(env, case_run_id, "heartbeat"),
        json=heartbeat_body(case_run_id, 1, attempt=3),
        headers=auth(env, attempt=3),
    )
    assert other_attempt.status_code in (403, 409)


# ---------------------------------------------------------------------------
# Manifest endpoint + token issuance
# ---------------------------------------------------------------------------


def test_manifest_endpoint_serves_stored_manifest_and_token(eval_env, client: TestClient) -> None:
    env = eval_env
    from app.models import Outbox

    dispatch_key = env["session"].query(Outbox).first().dispatch_key
    shard = env["shard"]

    response = client.get(
        f"/runner/v1/shards/{shard}/manifest",
        params={"eval_run_id": env["run_id"], "attempt": 1, "dispatch_key": dispatch_key},
    )
    assert response.status_code == 200
    served = response.content.decode()
    stored = env["session"].query(Outbox).first().payload_json["manifest_text"]
    assert served == stored  # byte-stable: digest stays valid for the runner
    validate = json.loads(served)
    assert validate["cases"][0]["case_run_id"] in env["cases"].values()

    token_header = response.headers.get("X-Runner-Task-Token")
    assert token_header
    from app.services.short_token import verify_short_token

    claims = verify_short_token(token_header, SECRET)
    assert claims["eval_run_id"] == env["run_id"]
    assert claims["shard_id"] == shard
    assert claims["attempt"] == 1
    assert claims["exp"] > datetime.now(UTC).timestamp() + 60


def test_manifest_endpoint_rejects_wrong_dispatch_key(eval_env, client: TestClient) -> None:
    env = eval_env
    response = client.get(
        f"/runner/v1/shards/{env['shard']}/manifest",
        params={"eval_run_id": env["run_id"], "attempt": 1, "dispatch_key": "forged"},
    )
    assert response.status_code in (404, 409)


def test_manifest_endpoint_refuses_cancelled_run(eval_env, client: TestClient) -> None:
    env = eval_env
    from app.models import Outbox

    evaluations.cancel_evaluation(env["session"], run_id=env["run_id"])
    dispatch_key = env["session"].query(Outbox).first().dispatch_key
    response = client.get(
        f"/runner/v1/shards/{env['shard']}/manifest",
        params={"eval_run_id": env["run_id"], "attempt": 1, "dispatch_key": dispatch_key},
    )
    assert response.status_code == 409


def test_case_callbacks_unified_on_platform_token(eval_env, client, configure_settings) -> None:
    """M2-04 遗留 b 收口：case 回调只认平台签发的短期 token——runner 面共享
    token（RUNNER_HEARTBEAT_TOKEN）不再守卫 case 端点（它只守卫 Runner 心跳）。"""

    env = eval_env
    case_run_id = env["cases"]["case-a"]
    configure_settings(RUNNER_HEARTBEAT_TOKEN="plane-token", RUNNER_TASK_SECRET=SECRET)

    # 平台短期 token：200（无需 runner 面 token）
    ok = client.post(
        case_url(env, case_run_id, "started"),
        json=started_body(env, case_run_id, event_id="evt_unified_started"),
        headers=auth(env),
    )
    assert ok.status_code == 200, ok.text

    # 只有 runner 面 token：401（case 端点不再接受它）
    plane_only = client.post(
        case_url(env, case_run_id, "heartbeat"),
        json=heartbeat_body(case_run_id, 1),
        headers={"Authorization": "Bearer plane-token"},
    )
    assert plane_only.status_code == 401
    assert plane_only.json()["detail"]["error"]["code"] == "runner_token_invalid"

    # 无 token：401
    missing = client.post(
        case_url(env, case_run_id, "heartbeat"), json=heartbeat_body(case_run_id, 1)
    )
    assert missing.status_code == 401


def test_completion_clears_case_error_fields(eval_env, client) -> None:
    """成功完成应清掉旧失败 attempt 遗留在 case 行上的 error 字段。"""

    env = eval_env
    case_run_id = env["cases"]["case-a"]
    client.post(case_url(env, case_run_id, "started"), json=started_body(env, case_run_id), headers=auth(env))
    failed = client.post(
        case_url(env, case_run_id, "failed"),
        json=failure_body(case_run_id, retryable=True),
        headers=auth(env),
    )
    assert failed.json()["retry_queued"] is True

    # 重试 attempt（attempt_no=2）成功完成
    completion = completion_body(env, case_run_id, attempt=2)
    completion["event_id"] = "evt_retry_completed"
    ok = client.post(case_url(env, case_run_id, "completed"), json=completion, headers=auth(env, attempt=2))
    assert ok.status_code == 200, ok.text

    env["session"].expire_all()
    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.execution_status == "succeeded"
    assert case.error_code is None
    assert case.error_summary is None

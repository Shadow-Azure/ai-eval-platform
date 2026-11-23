"""M2-04 fixed Linux runner heartbeat, state machine and query API.

The payload shape is pinned to the deployed heartbeat agent
(/opt/ai-eval-platform-runner/heartbeat.mjs): it POSTs
`{runner_id, name, labels, status, current_case_attempt_id}` to
`/runner/v1/runners/heartbeat` with `Authorization: Bearer RUNNER_HEARTBEAT_TOKEN`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import Settings, get_settings
from app.defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from app.model_gateway.tokens import verify_gateway_token
from app.models import (
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    ModelConfig,
    Outbox,
    ProvisionedInstance,
    ResourcePool,
    Runner,
)
from app.seed import ensure_default_scope
from app.services.short_token import verify_short_token
from case_helpers import EVAL_ENV_SECRET, auth
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "runner"

AGENT_PAYLOAD_DEFAULTS = {
    "runner_id": "fixed-linux-eval",
    "name": "fixed-linux-eval",
    "labels": ["self-hosted", "linux", "eval"],
    "status": "online",
    "current_case_attempt_id": None,
}


def agent_payload(**overrides) -> dict:
    payload = dict(AGENT_PAYLOAD_DEFAULTS)
    payload.update(overrides)
    return payload


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


@pytest.fixture
def configure_settings(monkeypatch: pytest.MonkeyPatch):
    """Override Settings via environment (they are cached with lru_cache)."""

    def _configure(**overrides: str) -> None:
        for key, value in overrides.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()

    yield _configure
    get_settings.cache_clear()


def backdate_last_heartbeat(session: Session, seconds: int) -> None:
    for runner in session.query(Runner).all():
        runner.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=seconds)
    session.commit()


# ---------------------------------------------------------------------------
# Runner-level heartbeat from the deployed agent
# ---------------------------------------------------------------------------


def test_heartbeat_registers_runner_and_reports_online(seeded: Session, client: TestClient) -> None:
    response = client.post("/runner/v1/runners/heartbeat", json=agent_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "online"
    assert body["runner_id"] == "fixed-linux-eval"

    runner = seeded.query(Runner).one()
    assert runner.provider_runner_id == "fixed-linux-eval"
    assert runner.name == "fixed-linux-eval"
    assert json.loads(json.dumps(runner.labels_json)) == ["self-hosted", "linux", "eval"]
    assert runner.status == "online"
    assert runner.last_heartbeat_at is not None
    assert runner.busy_case_attempt_id is None
    assert runner.registered_at is not None


def test_heartbeat_upserts_single_runner_row(seeded: Session, client: TestClient) -> None:
    first = client.post("/runner/v1/runners/heartbeat", json=agent_payload())
    assert first.status_code == 200
    second = client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(name="fixed-linux-eval-2", labels=["self-hosted", "linux"]),
    )
    assert second.status_code == 200

    runners = seeded.query(Runner).all()
    assert len(runners) == 1
    assert runners[0].name == "fixed-linux-eval-2"
    assert json.loads(json.dumps(runners[0].labels_json)) == ["self-hosted", "linux"]
    assert second.json()["last_heartbeat_at"] >= first.json()["last_heartbeat_at"]


def test_heartbeat_requires_token_when_configured(
    seeded: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(RUNNER_HEARTBEAT_TOKEN="platform-side-token")

    missing = client.post("/runner/v1/runners/heartbeat", json=agent_payload())
    wrong = client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(),
        headers={"Authorization": "Bearer not-the-token"},
    )
    good = client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(),
        headers={"Authorization": "Bearer platform-side-token"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json()["detail"]["error"]["code"] == "runner_token_invalid"
    assert good.status_code == 200


def test_heartbeat_open_when_no_token_configured(seeded: Session, client: TestClient) -> None:
    response = client.post("/runner/v1/runners/heartbeat", json=agent_payload())
    assert response.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        agent_payload(runner_id=""),
        {"name": "missing-runner-id"},
        agent_payload(status="busy", current_case_attempt_id=None),
        agent_payload(labels="self-hosted,linux"),
        agent_payload(status="detecting"),
    ],
)
def test_heartbeat_rejects_invalid_payload(
    seeded: Session, client: TestClient, payload: dict
) -> None:
    response = client.post("/runner/v1/runners/heartbeat", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Query API: online status, labels, latest heartbeat, current task
# ---------------------------------------------------------------------------


def test_list_runners_shows_status_labels_heartbeat_and_task(
    seeded: Session, client: TestClient
) -> None:
    client.post("/runner/v1/runners/heartbeat", json=agent_payload())
    client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(status="busy", current_case_attempt_id="attempt-41"),
    )

    response = client.get("/api/v1/runners")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert len(body["runners"]) == 1
    runner = body["runners"][0]
    assert runner["provider_runner_id"] == "fixed-linux-eval"
    assert runner["name"] == "fixed-linux-eval"
    assert runner["labels"] == ["self-hosted", "linux", "eval"]
    assert runner["status"] == "busy"
    assert runner["current_case_attempt_id"] == "attempt-41"
    assert runner["last_heartbeat_at"] is not None
    assert 0 <= runner["heartbeat_age_seconds"] < 5


def test_list_runners_empty(seeded: Session, client: TestClient) -> None:
    response = client.get("/api/v1/runners")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "runners": []}


def test_get_runner_detail(seeded: Session, client: TestClient) -> None:
    client.post("/runner/v1/runners/heartbeat", json=agent_payload())
    response = client.get("/api/v1/runners/fixed-linux-eval")
    assert response.status_code == 200
    assert response.json()["provider_runner_id"] == "fixed-linux-eval"
    assert client.get("/api/v1/runners/unknown-runner").status_code == 404


# ---------------------------------------------------------------------------
# State machine: busy / single job / offline sweep / recovery
# ---------------------------------------------------------------------------


def test_busy_transition_and_single_job_constraint(seeded: Session, client: TestClient) -> None:
    url = "/runner/v1/runners/heartbeat"

    busy = client.post(url, json=agent_payload(status="busy", current_case_attempt_id="attempt-41"))
    assert busy.status_code == 200
    assert busy.json()["status"] == "busy"

    again_same_job = client.post(
        url, json=agent_payload(status="busy", current_case_attempt_id="attempt-41")
    )
    assert again_same_job.status_code == 200

    second_job = client.post(
        url, json=agent_payload(status="busy", current_case_attempt_id="attempt-42")
    )
    assert second_job.status_code == 409
    assert second_job.json()["detail"]["error"]["code"] == "runner_busy_conflict"

    seeded.expire_all()
    runner = seeded.query(Runner).one()
    assert runner.busy_case_attempt_id == "attempt-41"

    back_online = client.post(url, json=agent_payload())
    assert back_online.status_code == 200
    assert back_online.json()["status"] == "online"
    seeded.expire_all()
    # M8-38: the bootstrap heartbeat is online while the job runs; it must not
    # erase the binding. Only a terminal callback/offline heartbeat releases it.
    assert seeded.query(Runner).one().busy_case_attempt_id == "attempt-41"


def test_two_runners_can_be_busy_independently(seeded: Session, client: TestClient) -> None:
    client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(runner_id="runner-a", name="runner-a", status="busy", current_case_attempt_id="a-1"),
    )
    client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(runner_id="runner-b", name="runner-b", status="busy", current_case_attempt_id="b-1"),
    )

    body = client.get("/api/v1/runners").json()
    statuses = {runner["provider_runner_id"]: runner["status"] for runner in body["runners"]}
    assert statuses == {"runner-a": "busy", "runner-b": "busy"}


def test_stale_runner_sweeps_to_offline_and_keeps_task_for_diagnosis(
    seeded: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(RUNNER_HEARTBEAT_TIMEOUT_SECONDS="90")
    client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(status="busy", current_case_attempt_id="attempt-41"),
    )

    backdate_last_heartbeat(seeded, seconds=91)
    body = client.get("/api/v1/runners").json()
    runner = body["runners"][0]
    assert runner["status"] == "offline"
    assert runner["current_case_attempt_id"] == "attempt-41"


def test_offline_runner_recovers_on_next_heartbeat(
    seeded: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(RUNNER_HEARTBEAT_TIMEOUT_SECONDS="90")
    client.post("/runner/v1/runners/heartbeat", json=agent_payload())
    backdate_last_heartbeat(seeded, seconds=200)
    assert client.get("/api/v1/runners").json()["runners"][0]["status"] == "offline"

    recovered = client.post("/runner/v1/runners/heartbeat", json=agent_payload())
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "online"
    assert client.get("/api/v1/runners").json()["runners"][0]["status"] == "online"


def test_explicit_offline_heartbeat_clears_current_task(seeded: Session, client: TestClient) -> None:
    client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(status="busy", current_case_attempt_id="attempt-41"),
    )
    shutdown = client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(status="offline"),
    )
    assert shutdown.status_code == 200
    body = client.get("/api/v1/runners").json()["runners"][0]
    assert body["status"] == "offline"
    assert body["current_case_attempt_id"] is None


def test_sweep_stale_runners_service_function(seeded: Session, client: TestClient) -> None:
    from app.services.runners import sweep_stale_runners

    client.post("/runner/v1/runners/heartbeat", json=agent_payload())
    client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(runner_id="runner-b", name="runner-b", status="busy", current_case_attempt_id="b-1"),
    )
    backdate_last_heartbeat(seeded, seconds=300)

    swept = sweep_stale_runners(seeded, timeout_seconds=90)
    assert swept == 2
    statuses = {
        runner.provider_runner_id: runner.status for runner in seeded.query(Runner).all()
    }
    assert statuses == {"fixed-linux-eval": "offline", "runner-b": "offline"}

    assert sweep_stale_runners(seeded, timeout_seconds=90) == 0


# ---------------------------------------------------------------------------
# M8-48 (#313) offline-runner retention cleanup
# ---------------------------------------------------------------------------


def _retention_runner(
    session: Session,
    *,
    pool: ResourcePool,
    name: str,
    status: str = "offline",
    age: timedelta = timedelta(days=10),
    now: datetime | None = None,
) -> Runner:
    now = now or datetime.now(UTC)
    runner = Runner(
        tenant_id=pool.tenant_id,
        resource_pool_id=pool.id,
        provider_runner_id=name,
        name=name,
        labels_json=["self-hosted", "spot"],
        status=status,
        last_heartbeat_at=now - age,
        registered_at=now - age,
        deregistered_at=now - age,
    )
    session.add(runner)
    session.flush()
    return runner


def test_cleanup_offline_runners_deletes_expired_rows_and_is_idempotent(
    seeded: Session,
) -> None:
    from app.services.runners import cleanup_offline_runners, ensure_fixed_pool

    pool = ensure_fixed_pool(seeded)
    now = datetime.now(UTC)
    expired = _retention_runner(seeded, pool=pool, name="expired", now=now)
    recent = _retention_runner(
        seeded, pool=pool, name="recent", age=timedelta(hours=1), now=now
    )
    online = _retention_runner(
        seeded, pool=pool, name="online", status="online", age=timedelta(days=30), now=now
    )

    settings = Settings(_env_file=None)
    assert cleanup_offline_runners(seeded, settings=settings, now=now) == 1
    seeded.expire_all()
    assert seeded.get(Runner, expired.id) is None
    assert seeded.get(Runner, recent.id) is not None
    assert seeded.get(Runner, online.id) is not None

    # A second pass has nothing left to delete.
    assert cleanup_offline_runners(seeded, settings=settings, now=now) == 0


def test_cleanup_offline_runners_keeps_live_references(seeded: Session) -> None:
    from app.services.runners import cleanup_offline_runners, ensure_fixed_pool

    tenant, _workspace, project = ensure_default_scope(seeded)
    pool = ensure_fixed_pool(seeded)
    now = datetime.now(UTC)
    active_attempt_runner = _retention_runner(
        seeded, pool=pool, name="active-attempt", now=now
    )
    active_instance_runner = _retention_runner(
        seeded, pool=pool, name="active-instance", now=now
    )

    run = EvalRun(
        tenant_id=tenant.id,
        project_id=project.id,
        resource_pool_id=pool.id,
        name="cleanup-run",
        status="running",
        requested_concurrency=1,
        total_cases=1,
        queued_count=0,
        idempotency_key=f"cleanup-{now.timestamp()}",
        created_by="test",
    )
    seeded.add(run)
    seeded.flush()
    case = EvalCaseRun(
        tenant_id=tenant.id,
        eval_run_id=run.id,
        dataset_case_id="case-1",
        case_key="case-1",
        ordinal=0,
    )
    seeded.add(case)
    seeded.flush()
    seeded.add(
        EvalCaseAttempt(
            tenant_id=tenant.id,
            eval_case_run_id=case.id,
            attempt_no=1,
            status="running",
            resource_pool_id=pool.id,
            runner_id=active_attempt_runner.id,
        )
    )
    seeded.add(
        ProvisionedInstance(
            tenant_id=tenant.id,
            resource_pool_id=pool.id,
            provider_instance_id="i-live",
            status="online",
            runner_id=active_instance_runner.id,
            cost_json={},
        )
    )
    seeded.flush()

    settings = Settings(_env_file=None)
    assert cleanup_offline_runners(seeded, settings=settings, now=now) == 0
    seeded.expire_all()
    assert seeded.get(Runner, active_attempt_runner.id) is not None
    assert seeded.get(Runner, active_instance_runner.id) is not None


def test_cleanup_offline_runners_detaches_terminal_history(seeded: Session) -> None:
    from app.services.runners import cleanup_offline_runners, ensure_fixed_pool

    tenant, _workspace, project = ensure_default_scope(seeded)
    pool = ensure_fixed_pool(seeded)
    now = datetime.now(UTC)
    runner = _retention_runner(seeded, pool=pool, name="terminal-history", now=now)

    instance = ProvisionedInstance(
        tenant_id=tenant.id,
        resource_pool_id=pool.id,
        provider_instance_id="i-released",
        status="released",
        runner_id=runner.id,
        cost_json={"runner_name": "terminal-history"},
    )
    seeded.add(instance)
    run = EvalRun(
        tenant_id=tenant.id,
        project_id=project.id,
        resource_pool_id=pool.id,
        name="cleanup-history-run",
        status="completed",
        requested_concurrency=1,
        total_cases=1,
        queued_count=0,
        idempotency_key=f"cleanup-history-{now.timestamp()}",
        created_by="test",
    )
    seeded.add(run)
    seeded.flush()
    case = EvalCaseRun(
        tenant_id=tenant.id,
        eval_run_id=run.id,
        dataset_case_id="case-h",
        case_key="case-h",
        ordinal=0,
    )
    seeded.add(case)
    seeded.flush()
    attempt = EvalCaseAttempt(
        tenant_id=tenant.id,
        eval_case_run_id=case.id,
        attempt_no=1,
        status="succeeded",
        runner_id=runner.id,
        resource_pool_id=pool.id,
    )
    seeded.add(attempt)
    seeded.flush()

    settings = Settings(_env_file=None)
    assert cleanup_offline_runners(seeded, settings=settings, now=now) == 1
    seeded.expire_all()
    assert seeded.get(Runner, runner.id) is None
    refreshed_attempt = seeded.get(EvalCaseAttempt, attempt.id)
    assert refreshed_attempt is not None and refreshed_attempt.runner_id is None
    refreshed_instance = seeded.get(ProvisionedInstance, instance.id)
    assert refreshed_instance is not None and refreshed_instance.runner_id is None
    # The instance keeps the diagnostic trail without the FK.
    assert refreshed_instance.cost_json["runner_name"] == "terminal-history"


# ---------------------------------------------------------------------------
# Contract schema consumption (runner-contract/schemas/v1) + case callbacks
# ---------------------------------------------------------------------------


def read_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_schema_validator_consumes_runner_contract_files() -> None:
    from app.runner_contracts import ContractInvalid, validate_payload

    validate_payload("heartbeat", read_fixture("heartbeat.valid.json"))
    validate_payload("completion", read_fixture("completion.valid.json"))
    validate_payload("failure", read_fixture("failure.valid.json"))
    validate_payload("upload", read_fixture("upload.valid.json"))
    validate_payload("dispatch", read_fixture("dispatch.valid.json"))
    validate_payload("manifest", read_fixture("manifest.valid.json"))

    with pytest.raises(ContractInvalid) as excinfo:
        validate_payload("heartbeat", read_fixture("heartbeat.invalid-sequence.json"))
    assert "sequence" in "; ".join(excinfo.value.errors)


def test_schema_validator_enforces_subset_semantics() -> None:
    from app.runner_contracts import ContractInvalid, validate_payload

    base = read_fixture("heartbeat.valid.json")

    missing_required = {k: v for k, v in base.items() if k != "event_id"}
    with pytest.raises(ContractInvalid) as excinfo:
        validate_payload("heartbeat", missing_required)
    assert "event_id" in "; ".join(excinfo.value.errors)

    extra = {**base, "model_api_key": "must-not-leak"}
    with pytest.raises(ContractInvalid) as excinfo:
        validate_payload("heartbeat", extra)
    assert "model_api_key" in "; ".join(excinfo.value.errors)

    bad_time = {**base, "occurred_at": "not-a-timestamp"}
    with pytest.raises(ContractInvalid):
        validate_payload("heartbeat", bad_time)

    wrong_const = {**base, "schema_version": "2.0"}
    with pytest.raises(ContractInvalid):
        validate_payload("heartbeat", wrong_const)


def make_heartbeat_body(case_run_id: str = "ecr_001", sequence: int = 1) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": f"evt_{case_run_id}_hb_{sequence}",
        "case_run_id": case_run_id,
        "attempt": 1,
        "sequence": sequence,
        "phase": "executing",
        "progress": {"completed": 0, "total": 1},
        "occurred_at": datetime.now(UTC).isoformat(),
    }



def make_case_env(session: Session) -> dict:
    """M2-05: case callbacks resolve a real evaluation; the runner-binding
    semantics under test are unchanged, but case ids must exist."""
    from app.services import evaluations

    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="runner 面冒烟",
        case_keys=["case-a", "case-b"],
        idempotency_key="runner-plane",
        created_by="agent",
        settings=get_settings(),
    )
    cases = {
        c.case_key: c.id
        for c in session.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id).all()
    }
    return cases


def eval_session_override(client: TestClient, session: Session) -> None:
    """Ensure the app writes through a session that shares the test DB.

    The conftest ``client`` fixture already overrides get_session against the
    same engine, so no extra wiring is needed; this helper exists to make the
    dependency explicit in the tests below.
    """

def bind_runner_via_agent(client: TestClient, attempt: str | None = None) -> None:
    payload = agent_payload(status="busy", current_case_attempt_id=attempt) if attempt else agent_payload()
    assert client.post("/runner/v1/runners/heartbeat", json=payload).status_code == 200


def test_case_heartbeat_binds_runner_and_marks_busy(seeded: Session, client: TestClient) -> None:
    cases = make_case_env(seeded)
    bind_runner_via_agent(client)
    response = client.post(
        f"/runner/v1/case-runs/{cases['case-a']}/heartbeat",
        json=make_heartbeat_body(cases["case-a"]),
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["case_run_id"] == cases["case-a"]
    assert body["runner_id"] == "fixed-linux-eval"
    assert body["runner_status"] == "busy"
    listed = client.get("/api/v1/runners/fixed-linux-eval").json()
    assert listed["status"] == "busy"
    assert listed["current_case_attempt_id"] == cases["case-a"]


def test_case_heartbeat_without_runner_header_is_accepted(seeded: Session, client: TestClient) -> None:
    cases = make_case_env(seeded)
    response = client.post(
        f"/runner/v1/case-runs/{cases['case-a']}/heartbeat",
        json=make_heartbeat_body(cases["case-a"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["runner_id"] is None


def test_case_heartbeat_rejects_contract_violations(seeded: Session, client: TestClient) -> None:
    bind_runner_via_agent(client)
    bad = make_heartbeat_body()
    bad["sequence"] = -1
    response = client.post(
        "/runner/v1/case-runs/ecr_001/heartbeat",
        json=bad,
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "contract_invalid"

    extra = {**make_heartbeat_body(), "model_api_key": "leak"}
    response = client.post(
        "/runner/v1/case-runs/ecr_001/heartbeat",
        json=extra,
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 422


def test_case_heartbeat_conflicts_with_bound_other_job(seeded: Session, client: TestClient) -> None:
    cases = make_case_env(seeded)
    bind_runner_via_agent(client, attempt="attempt-41")
    response = client.post(
        f"/runner/v1/case-runs/{cases['case-b']}/heartbeat",
        json=make_heartbeat_body(cases["case-b"]),
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "runner_busy_conflict"


def test_case_completed_and_failed_release_runner(seeded: Session, client: TestClient) -> None:
    cases = make_case_env(seeded)
    bind_runner_via_agent(client)
    headers = {"X-Runner-Id": "fixed-linux-eval"}

    completion = read_fixture("completion.valid.json")
    completion["case_run_id"] = cases["case-a"]
    completed = client.post(
        f"/runner/v1/case-runs/{cases['case-a']}/completed",
        json=completion,
        headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["runner_status"] == "online"

    bind_runner_via_agent(client, attempt=cases["case-a"])
    failure = read_fixture("failure.valid.json")
    failure["case_run_id"] = cases["case-b"]
    failed = client.post(
        f"/runner/v1/case-runs/{cases['case-b']}/failed",
        json=failure,
        headers=headers,
    )
    assert failed.status_code == 200, failed.text
    assert client.get("/api/v1/runners/fixed-linux-eval").json()["status"] == "online"


def test_case_uploads_callback_accepted(seeded: Session, client: TestClient) -> None:
    cases = make_case_env(seeded)
    bind_runner_via_agent(client, attempt=cases["case-a"])
    upload = read_fixture("upload.valid.json")
    upload["case_run_id"] = cases["case-a"]
    response = client.post(
        f"/runner/v1/case-runs/{cases['case-a']}/uploads",
        json=upload,
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["runner_status"] == "busy"


def test_case_callback_unknown_runner_404(seeded: Session, client: TestClient) -> None:
    cases = make_case_env(seeded)
    response = client.post(
        f"/runner/v1/case-runs/{cases['case-a']}/heartbeat",
        json=make_heartbeat_body(cases["case-a"]),
        headers={"X-Runner-Id": "ghost-runner"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "runner_not_found"


def test_case_callback_unknown_case_is_404(seeded: Session, client: TestClient) -> None:
    response = client.post(
        "/runner/v1/case-runs/ecr_missing/heartbeat", json=make_heartbeat_body("ecr_missing")
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "case_run_not_found"


def test_case_callbacks_require_token_when_configured(
    seeded: Session, client: TestClient, configure_settings
) -> None:
    """M2-05 起 case 回调统一为平台签发的短期 token（RUNNER_TASK_SECRET 配置
    后强制）；runner 面 token 只守卫 Runner 心跳，不再守卫 case 端点。"""

    configure_settings(RUNNER_TASK_SECRET="task-secret", RUNNER_HEARTBEAT_TOKEN="platform-side-token")
    plane_auth = {"Authorization": "Bearer platform-side-token"}
    registered = client.post("/runner/v1/runners/heartbeat", json=agent_payload(), headers=plane_auth)
    assert registered.status_code == 200

    cases = make_case_env(seeded)
    no_token = client.post(
        f"/runner/v1/case-runs/{cases['case-a']}/heartbeat",
        json=make_heartbeat_body(cases["case-a"]),
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert no_token.status_code == 401

    plane_only = client.post(
        f"/runner/v1/case-runs/{cases['case-a']}/heartbeat",
        json=make_heartbeat_body(cases["case-a"]),
        headers={"X-Runner-Id": "fixed-linux-eval", **plane_auth},
    )
    assert plane_only.status_code == 401

    # RUNNER_TASK_SECRET 未配置（开发态）时 case 端点保持开放。
    configure_settings(RUNNER_TASK_SECRET="")
    open_dev = client.post(
        f"/runner/v1/case-runs/{cases['case-a']}/heartbeat",
        json=make_heartbeat_body(cases["case-a"]),
    )
    assert open_dev.status_code == 200, open_dev.text


def test_case_callback_rejects_path_payload_mismatch(seeded: Session, client: TestClient) -> None:
    cases = make_case_env(seeded)
    bind_runner_via_agent(client)
    body = make_heartbeat_body(cases["case-a"])
    response = client.post(
        "/runner/v1/case-runs/ecr_other/heartbeat",
        json=body,
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "contract_invalid"


# ---------------------------------------------------------------------------
# Worker wiring: periodic offline sweep
# ---------------------------------------------------------------------------


def test_worker_sweep_once_uses_configured_timeout(
    seeded: Session, client: TestClient, configure_settings, engine
) -> None:
    from app.worker.main import sweep_once

    configure_settings(RUNNER_HEARTBEAT_TIMEOUT_SECONDS="120")
    client.post("/runner/v1/runners/heartbeat", json=agent_payload())

    backdate_last_heartbeat(seeded, seconds=100)
    with Session(engine) as worker_session:
        assert sweep_once(worker_session) == 0

    backdate_last_heartbeat(seeded, seconds=130)
    with Session(engine) as worker_session:
        assert sweep_once(worker_session) == 1


def test_worker_runner_cleanup_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M8-48 (#313): the retention pass runs once per configured interval."""

    from app.worker import main as worker_main

    calls: list[int] = []

    def _fake_cleanup(session, *, settings, now=None, batch_size=None):
        calls.append(1)
        return 1

    monkeypatch.setattr(worker_main, "cleanup_offline_runners", _fake_cleanup)
    monkeypatch.setattr(worker_main, "_last_runner_cleanup_at", None)

    assert worker_main.runner_cleanup_once(None) == 1
    assert worker_main.runner_cleanup_once(None) == 0
    assert worker_main.runner_cleanup_once(None, force=True) == 1
    assert len(calls) == 2


def test_heartbeat_adopts_runner_row_left_in_stale_pool(
    seeded: Session, client: TestClient
) -> None:
    """A leftover row for the same machine under another pool must be adopted,
    not duplicated (seen with data left by an earlier M2-04 attempt)."""
    from app.models import ResourcePool
    from app.services.runners import ensure_fixed_pool

    old_pool = ResourcePool(
        tenant_id=str(DEFAULT_TENANT_ID),
        project_id=str(DEFAULT_PROJECT_ID),
        name="stale-attempt-pool",
        provider_key="fixed",
        labels_json={},
        status="active",
    )
    seeded.add(old_pool)
    seeded.flush()
    stale = Runner(
        tenant_id=str(DEFAULT_TENANT_ID),
        resource_pool_id=old_pool.id,
        provider_runner_id="fixed-linux-eval",
        name="fixed-linux-eval",
        labels_json=[],
        status="offline",
    )
    seeded.add(stale)
    seeded.commit()

    response = client.post("/runner/v1/runners/heartbeat", json=agent_payload())
    assert response.status_code == 200

    seeded.expire_all()
    rows = seeded.query(Runner).filter(Runner.provider_runner_id == "fixed-linux-eval").all()
    assert len(rows) == 1
    adopted = rows[0]
    assert adopted.resource_pool_id == ensure_fixed_pool(seeded).id
    assert adopted.status == "online"
    assert json.loads(json.dumps(adopted.labels_json)) == ["self-hosted", "linux", "eval"]


def test_ensure_fixed_pool_registers_unique_runner_labels(seeded: Session) -> None:
    """The system fixed pool must own a label no other pool registers."""

    from app.services.runners import ensure_fixed_pool

    pool = ensure_fixed_pool(seeded)
    seeded.flush()

    assert json.loads(json.dumps(pool.labels_json)) == {
        "runs_on": ["self-hosted", "linux", "eval", "pool-fixed-linux"]
    }


# ---------------------------------------------------------------------------
# M4-04: spot runners join the pool that created them
# ---------------------------------------------------------------------------


def _spot_pool(seeded: Session) -> ResourcePool:
    pool = ResourcePool(
        tenant_id=str(DEFAULT_TENANT_ID),
        project_id=str(DEFAULT_PROJECT_ID),
        name="spot-heartbeat-pool",
        provider_key="aliyun",
        provisioning_mode="spot",
        min_capacity=0,
        max_capacity=2,
        instance_config_json={"instance_type": "ecs.g7.large"},
        labels_json={"runs_on": ["self-hosted", "linux", "eval"]},
        status="active",
    )
    seeded.add(pool)
    seeded.commit()
    return pool


def test_spot_runner_heartbeat_binds_to_its_pool(seeded: Session, client: TestClient) -> None:
    pool = _spot_pool(seeded)
    response = client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(
            runner_id="spot-runner-1",
            name="spot-runner-1",
            resource_pool_id=pool.id,
        ),
    )
    assert response.status_code == 200, response.text
    seeded.expire_all()
    runner = seeded.query(Runner).filter(Runner.provider_runner_id == "spot-runner-1").one()
    assert runner.resource_pool_id == pool.id


def test_spot_runner_heartbeat_rejects_unknown_pool(seeded: Session, client: TestClient) -> None:
    response = client.post(
        "/runner/v1/runners/heartbeat",
        json=agent_payload(runner_id="spot-runner-x", resource_pool_id="missing-pool"),
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "resource_pool_not_found"


# ---------------------------------------------------------------------------
# M8-07: the frozen prompt suffix travels with every shard case task
# ---------------------------------------------------------------------------


def test_shard_task_payload_includes_prompt_suffix(
    eval_env: dict, session: Session, client: TestClient
) -> None:
    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run.id))
    assert outbox is not None

    def shard_cases() -> list[dict]:
        response = client.get(
            f"/runner/v1/shards/{eval_env['shard']}/tasks",
            params={
                "eval_run_id": run.id,
                "attempt": 1,
                "dispatch_key": outbox.dispatch_key,
            },
            headers=auth(eval_env),
        )
        assert response.status_code == 200, response.text
        return response.json()["cases"]

    assert {case["prompt_suffix"] for case in shard_cases()} == {""}

    suffix = "非交互评测：不要向用户提问，自主决策并继续。"
    run.prompt_suffix = suffix
    session.commit()
    cases = shard_cases()
    assert cases
    assert {case["prompt_suffix"] for case in cases} == {suffix}


# ---------------------------------------------------------------------------
# M8-31: the run-wide max turns travels with the shard task payload
# ---------------------------------------------------------------------------


def test_shard_task_payload_includes_max_turns(
    eval_env: dict, session: Session, client: TestClient
) -> None:
    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run.id))
    assert outbox is not None

    def payload() -> dict:
        response = client.get(
            f"/runner/v1/shards/{eval_env['shard']}/tasks",
            params={
                "eval_run_id": run.id,
                "attempt": 1,
                "dispatch_key": outbox.dispatch_key,
            },
            headers=auth(eval_env),
        )
        assert response.status_code == 200, response.text
        return response.json()

    assert payload()["max_turns"] == 200

    run.max_turns = 900
    session.commit()
    assert payload()["max_turns"] == 900


# ---------------------------------------------------------------------------
# M8-32: the run-wide Agent timeout travels with the shard task payload
# ---------------------------------------------------------------------------


def test_shard_task_payload_includes_agent_timeout(
    eval_env: dict, session: Session, client: TestClient
) -> None:
    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run.id))
    assert outbox is not None

    def payload() -> dict:
        response = client.get(
            f"/runner/v1/shards/{eval_env['shard']}/tasks",
            params={
                "eval_run_id": run.id,
                "attempt": 1,
                "dispatch_key": outbox.dispatch_key,
            },
            headers=auth(eval_env),
        )
        assert response.status_code == 200, response.text
        return response.json()

    assert payload()["agent_timeout_seconds"] == 1800
    assert isinstance(payload()["image_sources"], list)
    assert payload()["image_sources"] == json.loads(outbox.payload_json["manifest_text"])["image_sources"]

    run.agent_timeout_seconds = 4500
    session.commit()
    assert payload()["agent_timeout_seconds"] == 4500


# ---------------------------------------------------------------------------
# M8-14: the run-wide GitHub egress proxy travels with the shard task payload
# ---------------------------------------------------------------------------


def _shard_task_payload(eval_env: dict, session: Session, client: TestClient) -> dict:
    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run.id))
    assert outbox is not None
    response = client.get(
        f"/runner/v1/shards/{eval_env['shard']}/tasks",
        params={
            "eval_run_id": run.id,
            "attempt": 1,
            "dispatch_key": outbox.dispatch_key,
        },
        headers=auth(eval_env),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_shard_task_payload_includes_egress_proxy_when_configured(
    eval_env: dict, session: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(RUNNER_EGRESS_PROXY=" http://10.0.0.10:3128 ")

    payload = _shard_task_payload(eval_env, session, client)

    assert payload["egress_proxy"] == "http://10.0.0.10:3128"


def test_shard_task_payload_keeps_egress_proxy_unset_without_configuration(
    eval_env: dict, session: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(RUNNER_EGRESS_PROXY="")

    payload = _shard_task_payload(eval_env, session, client)

    assert payload.get("egress_proxy") is None
    assert payload.get("egress_token") is None


def test_shard_task_payload_issues_an_egress_token_with_the_case_token_ttl(
    eval_env: dict, session: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(RUNNER_EGRESS_PROXY="http://10.0.0.10:3128")
    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    run.case_timeout_seconds = 7200
    session.commit()

    payload = _shard_task_payload(eval_env, session, client)

    token = payload["egress_token"]
    assert isinstance(token, str) and token
    claims = verify_short_token(token, EVAL_ENV_SECRET)
    assert claims["scope"] == "egress"
    assert claims["eval_run_id"] == run.id
    assert claims["shard_id"] == eval_env["shard"]
    assert claims["attempt"] == 1
    assert claims["pool_id"] == (run.resource_pool_id or "default")
    # Mirrors _case_token_ttl: max(300, case_timeout_seconds + 900).
    remaining = int(claims["exp"]) - int(datetime.now(UTC).timestamp())
    assert 7200 + 900 - 5 <= remaining <= 7200 + 900


def test_runner_egress_proxy_setting_validation() -> None:
    assert Settings(runner_egress_proxy="").runner_egress_proxy is None
    assert (
        Settings(runner_egress_proxy="http://10.0.0.10:3128").runner_egress_proxy
        == "http://10.0.0.10:3128"
    )
    for invalid in [
        "10.0.0.10:3128",
        "ftp://10.0.0.10:3128",
        "http://user:secret@10.0.0.10:3128",
    ]:
        with pytest.raises(ValueError):
            Settings(runner_egress_proxy=invalid)


# ---------------------------------------------------------------------------
# M8-12: the case token must outlive the configured case timeout
# ---------------------------------------------------------------------------

def _token_exp(token: str) -> int:
    body = token.split(".")[1]
    padding = "=" * (-len(body) % 4)
    payload = json.loads(base64.urlsafe_b64decode(body + padding))
    return int(payload["exp"])


def test_manifest_case_token_ttl_covers_case_timeout(
    eval_env: dict, session: Session, client: TestClient
) -> None:
    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run.id))
    assert outbox is not None

    def token() -> str:
        response = client.get(
            f"/runner/v1/shards/{eval_env['shard']}/manifest",
            params={
                "eval_run_id": run.id,
                "attempt": 1,
                "dispatch_key": outbox.dispatch_key,
            },
            headers=auth(eval_env),
        )
        assert response.status_code == 200, response.text
        return response.headers["X-Runner-Task-Token"]

    run.case_timeout_seconds = 3600
    session.commit()
    assert _token_exp(token()) - int(datetime.now(UTC).timestamp()) >= 3600

    run.case_timeout_seconds = 60
    session.commit()
    assert _token_exp(token()) - int(datetime.now(UTC).timestamp()) >= 300


def test_manifest_serves_the_server_descriptor_for_a_minimal_dispatch(
    eval_env: dict, session: Session, client: TestClient
) -> None:
    """M8-51 (#323) option C: digest/expiry come from the manifest response."""

    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run.id))
    assert outbox is not None
    client_payload = outbox.payload_json["client_payload"]
    assert client_payload["schema_version"] == "1.1"
    assert "manifest_digest" not in client_payload
    assert "expires_at" not in client_payload

    response = client.get(
        f"/runner/v1/shards/{eval_env['shard']}/manifest",
        params={
            "eval_run_id": run.id,
            "attempt": 1,
            "dispatch_key": outbox.dispatch_key,
        },
        headers=auth(eval_env),
    )
    assert response.status_code == 200, response.text
    manifest_text = outbox.payload_json["manifest_text"]
    expected_digest = "sha256:" + hashlib.sha256(manifest_text.encode()).hexdigest()
    assert response.headers["X-Manifest-Digest"] == expected_digest
    assert response.headers["X-Manifest-Digest"] == outbox.payload_json["manifest_digest"]
    assert response.headers["X-Manifest-Expires-At"] == outbox.payload_json["expires_at"]


def test_manifest_serves_the_descriptor_for_a_legacy_dispatch(
    eval_env: dict, session: Session, client: TestClient
) -> None:
    """M8-51 (#323): an in-flight 1.0 row still gets descriptor headers.

    Legacy rows predate the top-level ``expires_at``; the endpoint must fall
    back to the expiry frozen in their 1.0 client payload so a runner started
    before the rollout can still verify the manifest.
    """

    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run.id))
    assert outbox is not None
    payload = dict(outbox.payload_json)
    client_payload = dict(payload["client_payload"])
    client_payload["schema_version"] = "1.0"
    client_payload["manifest_digest"] = payload["manifest_digest"]
    client_payload["expires_at"] = payload["expires_at"]
    payload["client_payload"] = client_payload
    payload.pop("expires_at", None)
    outbox.payload_json = payload
    session.commit()

    response = client.get(
        f"/runner/v1/shards/{eval_env['shard']}/manifest",
        params={
            "eval_run_id": run.id,
            "attempt": 1,
            "dispatch_key": outbox.dispatch_key,
        },
        headers=auth(eval_env),
    )
    assert response.status_code == 200, response.text
    assert response.headers["X-Manifest-Digest"] == payload["manifest_digest"]
    assert response.headers["X-Manifest-Expires-At"] == client_payload["expires_at"]


def test_manifest_model_token_ttl_covers_agent_timeout(
    eval_env: dict, session: Session, client: TestClient
) -> None:
    """M8-41: the model token must outlive the agent, not just image pull."""

    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run.id))
    assert outbox is not None

    config = ModelConfig(
        tenant_id=run.tenant_id,
        project_id=str(DEFAULT_PROJECT_ID),
        provider="anthropic",
        display_name="m8-41 test model",
        base_url="https://example.invalid",
        model_name="claude-test",
    )
    session.add(config)
    session.flush()
    run.model_config_id = config.id
    run.model_name = "claude-test"
    run.case_timeout_seconds = 3600
    run.agent_timeout_seconds = 1800
    session.commit()

    def model_token() -> str:
        response = client.get(
            f"/runner/v1/shards/{eval_env['shard']}/manifest",
            params={
                "eval_run_id": run.id,
                "attempt": 1,
                "dispatch_key": outbox.dispatch_key,
                "case_run_id": eval_env["cases"]["case-a"],
            },
            headers=auth(eval_env),
        )
        assert response.status_code == 200, response.text
        token = response.headers.get("X-Runner-Model-Token")
        assert token, "manifest did not issue a model token"
        return token

    now = int(datetime.now(UTC).timestamp())
    assert _token_exp(model_token()) - now >= 2099  # 1800 + 300 grace

    run.case_timeout_seconds = 600
    run.agent_timeout_seconds = 300
    session.commit()
    assert _token_exp(model_token()) - int(datetime.now(UTC).timestamp()) >= 599

    run.case_timeout_seconds = 60
    run.agent_timeout_seconds = 600
    session.commit()
    assert _token_exp(model_token()) - int(datetime.now(UTC).timestamp()) >= 59


def test_manifest_case_token_binding_and_legacy_retirement(
    eval_env: dict,
    session: Session,
    client: TestClient,
    configure_settings,
) -> None:
    """M8 (#358): per-case issuance, terminal case rejection, legacy cutoff."""

    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == run.id))
    assert outbox is not None
    config = ModelConfig(
        tenant_id=run.tenant_id,
        project_id=str(DEFAULT_PROJECT_ID),
        provider="anthropic",
        display_name="case token model",
        base_url="https://example.invalid",
        model_name="claude-test",
    )
    session.add(config)
    session.flush()
    run.model_config_id = config.id
    run.model_name = "claude-test"
    session.commit()
    case_run_id = eval_env["cases"]["case-a"]

    def manifest(case_id: str | None = None):
        params = {
            "eval_run_id": run.id,
            "attempt": 1,
            "dispatch_key": outbox.dispatch_key,
        }
        if case_id:
            params["case_run_id"] = case_id
        return client.get(
            f"/runner/v1/shards/{eval_env['shard']}/manifest",
            params=params,
            headers=auth(eval_env),
        )

    case_response = manifest(case_run_id)
    assert case_response.status_code == 200, case_response.text
    claims = verify_gateway_token(
        case_response.headers["X-Runner-Model-Token"],
        get_settings().gateway_secret,
    )
    assert claims.case_run_id == case_run_id
    assert claims.case_attempt_id is not None

    # A terminal case attempt is not handed a token it cannot use.
    attempt = session.scalar(
        select(EvalCaseAttempt).where(
            EvalCaseAttempt.eval_case_run_id == case_run_id
        )
    )
    assert attempt is not None
    attempt.status = "failed"
    attempt.ended_at = datetime.now(UTC)
    session.commit()
    stale = manifest(case_run_id)
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"]["code"] == "stale_attempt"

    # After the compatibility deadline, a manifest call without case_run_id
    # still serves the manifest but stops minting the shard-level model token.
    configure_settings(GATEWAY_LEGACY_TOKEN_DEADLINE="2020-01-01T00:00:00Z")
    legacy = manifest()
    assert legacy.status_code == 200
    assert "X-Runner-Model-Token" not in legacy.headers



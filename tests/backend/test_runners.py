"""M2-04 fixed Linux runner heartbeat, state machine and query API.

The payload shape is pinned to the deployed heartbeat agent
(/opt/ai-eval-platform-runner/heartbeat.mjs): it POSTs
`{runner_id, name, labels, status, current_case_attempt_id}` to
`/runner/v1/runners/heartbeat` with `Authorization: Bearer RUNNER_HEARTBEAT_TOKEN`.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import Settings, get_settings
from app.defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from app.models import EvalCaseRun, EvalRun, Outbox, ResourcePool, Runner
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
    assert seeded.query(Runner).one().busy_case_attempt_id is None


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


def test_ensure_fixed_pool_registers_explicit_runner_labels(seeded: Session) -> None:
    """A new system pool must not store the legacy provider marker as a label."""

    from app.services.runners import ensure_fixed_pool

    pool = ensure_fixed_pool(seeded)
    seeded.flush()

    assert json.loads(json.dumps(pool.labels_json)) == {
        "runs_on": ["self-hosted", "linux", "eval"]
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



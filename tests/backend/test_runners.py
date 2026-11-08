"""M2-04 fixed Linux runner heartbeat, state machine and query API.

The payload shape is pinned to the deployed heartbeat agent
(/opt/ai-eval-platform-runner/heartbeat.mjs): it POSTs
`{runner_id, name, labels, status, current_case_attempt_id}` to
`/runner/v1/runners/heartbeat` with `Authorization: Bearer RUNNER_HEARTBEAT_TOKEN`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import get_settings
from app.models import Runner
from app.seed import ensure_default_scope
from fastapi.testclient import TestClient
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


def bind_runner_via_agent(client: TestClient, attempt: str | None = None) -> None:
    payload = agent_payload(status="busy", current_case_attempt_id=attempt) if attempt else agent_payload()
    assert client.post("/runner/v1/runners/heartbeat", json=payload).status_code == 200


def test_case_heartbeat_binds_runner_and_marks_busy(seeded: Session, client: TestClient) -> None:
    bind_runner_via_agent(client)
    response = client.post(
        "/runner/v1/case-runs/ecr_001/heartbeat",
        json=make_heartbeat_body(),
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "case_run_id": "ecr_001",
        "runner_id": "fixed-linux-eval",
        "status": "busy",
    }
    listed = client.get("/api/v1/runners/fixed-linux-eval").json()
    assert listed["status"] == "busy"
    assert listed["current_case_attempt_id"] == "ecr_001"


def test_case_heartbeat_without_runner_header_is_accepted(seeded: Session, client: TestClient) -> None:
    response = client.post("/runner/v1/case-runs/ecr_001/heartbeat", json=make_heartbeat_body())
    assert response.status_code == 200
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
    bind_runner_via_agent(client, attempt="attempt-41")
    response = client.post(
        "/runner/v1/case-runs/ecr_002/heartbeat",
        json=make_heartbeat_body("ecr_002"),
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "runner_busy_conflict"


def test_case_completed_and_failed_release_runner(seeded: Session, client: TestClient) -> None:
    bind_runner_via_agent(client)
    headers = {"X-Runner-Id": "fixed-linux-eval"}

    completed = client.post(
        "/runner/v1/case-runs/ecr_001/completed",
        json=read_fixture("completion.valid.json"),
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "online"

    bind_runner_via_agent(client, attempt="ecr_001")
    failed = client.post(
        "/runner/v1/case-runs/ecr_001/failed",
        json=read_fixture("failure.valid.json"),
        headers=headers,
    )
    assert failed.status_code == 200
    assert client.get("/api/v1/runners/fixed-linux-eval").json()["status"] == "online"


def test_case_uploads_callback_accepted(seeded: Session, client: TestClient) -> None:
    bind_runner_via_agent(client, attempt="ecr_001")
    response = client.post(
        "/runner/v1/case-runs/ecr_001/uploads",
        json=read_fixture("upload.valid.json"),
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "busy"


def test_case_callback_unknown_runner_404(seeded: Session, client: TestClient) -> None:
    response = client.post(
        "/runner/v1/case-runs/ecr_001/heartbeat",
        json=make_heartbeat_body(),
        headers={"X-Runner-Id": "ghost-runner"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "runner_not_found"


def test_case_callbacks_require_token_when_configured(
    seeded: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(RUNNER_HEARTBEAT_TOKEN="platform-side-token")
    auth = {"Authorization": "Bearer platform-side-token"}
    registered = client.post("/runner/v1/runners/heartbeat", json=agent_payload(), headers=auth)
    assert registered.status_code == 200

    response = client.post(
        "/runner/v1/case-runs/ecr_001/heartbeat",
        json=make_heartbeat_body(),
        headers={"X-Runner-Id": "fixed-linux-eval"},
    )
    assert response.status_code == 401
    ok = client.post(
        "/runner/v1/case-runs/ecr_001/heartbeat",
        json=make_heartbeat_body(),
        headers={"X-Runner-Id": "fixed-linux-eval", **auth},
    )
    assert ok.status_code == 200


def test_case_callback_rejects_path_payload_mismatch(seeded: Session, client: TestClient) -> None:
    bind_runner_via_agent(client)
    body = make_heartbeat_body("ecr_001")
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

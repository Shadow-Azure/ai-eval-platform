"""M2-05 minimal synthetic evaluation CRUD + transactional shard outbox.

Creating an evaluation writes run/case/attempt/outbox rows in ONE transaction:
the outbox rows already carry the contract-valid repository_dispatch payload
and the byte-stable manifest text, so a crash right after commit can be
reconciled without regenerating anything (验收 2：事务 outbox 生成分片派发).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from app.models import (
    ATTEMPT_STATUSES,
    Dataset,
    DatasetVersion,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    Outbox,
    ResourcePool,
)
from app.runner_contracts import validate_payload
from app.seed import ensure_default_scope
from app.services import evaluations
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

CASE_KEYS = ["case-alpha", "case-beta", "case-gamma"]


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def create_eval(
    session: Session,
    *,
    case_keys: list[str] | None = None,
    shard_size: int | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
):
    settings = get_settings()
    return evaluations.create_synthetic_evaluation(
        session,
        name="合成冒烟",
        case_keys=case_keys or CASE_KEYS,
        shard_size=shard_size,
        idempotency_key=idempotency_key,
        created_by="agent",
        settings=settings,
        now=now,
    )


# ---------------------------------------------------------------------------
# Creation: one transaction builds run/case/attempt/outbox
# ---------------------------------------------------------------------------


def test_create_builds_run_cases_attempts_and_contract_outbox(
    seeded: Session, configure_settings
) -> None:
    configure_settings(RUNNER_CALLBACK_BASE_URL="http://127.0.0.1:8000")
    run, created = create_eval(seeded, shard_size=2, idempotency_key="idem-1")
    assert created is True
    assert run.status == "queued"
    assert run.total_cases == 3
    assert run.queued_count == 3

    cases = seeded.query(EvalCaseRun).filter(EvalCaseRun.eval_run_id == run.id).all()
    assert [c.case_key for c in sorted(cases, key=lambda c: c.ordinal)] == CASE_KEYS

    attempts = seeded.query(EvalCaseAttempt).all()
    assert len(attempts) == 3
    for attempt in attempts:
        assert attempt.attempt_no == 1
        assert attempt.status == "pending"
        assert attempt.shard_id
        assert attempt.dispatch_key
    assert len({a.dispatch_key for a in attempts}) == 3

    outbox_rows = seeded.query(Outbox).all()
    assert len(outbox_rows) == 2  # 3 cases / shard_size 2 → shards [2, 1]
    for row in outbox_rows:
        assert row.topic == "eval.shard.dispatch"
        assert row.status == "pending"
        assert row.aggregate_id == run.id

        payload = row.payload_json
        client_payload = payload["client_payload"]
        validate_payload("dispatch", client_payload)
        assert client_payload["runs_on"] == ["self-hosted", "linux", "eval"]
        assert client_payload["eval_run_id"] == run.id
        assert client_payload["attempt"] == 1
        assert client_payload["dispatch_key"] == row.dispatch_key

        manifest_text = payload["manifest_text"]
        manifest = json.loads(manifest_text)
        validate_payload("manifest", manifest)
        assert manifest["runs_on"] == ["self-hosted", "linux", "eval"]
        import hashlib

        assert client_payload["manifest_digest"] == (
            "sha256:" + hashlib.sha256(manifest_text.encode()).hexdigest()
        )
        assert client_payload["manifest_url"].startswith("http://127.0.0.1:8000/runner/v1/shards/")
        assert client_payload["expires_at"] > datetime.now(UTC).isoformat()

        shard_cases = manifest["cases"]
        assert len(shard_cases) >= 1
        for item in shard_cases:
            case_run = seeded.query(EvalCaseRun).filter(EvalCaseRun.id == item["case_run_id"]).one()
            assert case_run.case_key == item["case_key"]
            attempt = (
                seeded.query(EvalCaseAttempt)
                .filter(EvalCaseAttempt.eval_case_run_id == case_run.id)
                .one()
            )
            assert attempt.shard_id == client_payload["shard_id"]

    events = seeded.query(EvalEvent).filter(EvalEvent.eval_run_id == run.id).all()
    assert [e.event_type for e in events] == ["eval.created"]


def test_dispatch_freezes_custom_pool_labels(seeded: Session, configure_settings) -> None:
    """M5-08: a custom-label spot pool must receive its own execute jobs."""

    configure_settings()
    run, _ = create_eval(seeded, shard_size=8, idempotency_key="custom-labels")
    pool = ResourcePool(
        id="pool-custom-labels",
        tenant_id=str(DEFAULT_TENANT_ID),
        project_id=str(DEFAULT_PROJECT_ID),
        name="custom-labels",
        provider_key="aliyun",
        provisioning_mode="spot",
        labels_json={"runs_on": ["self-hosted", "gpu", "eval"]},
    )
    seeded.add(pool)
    seeded.flush()
    run.resource_pool_id = pool.id
    seeded.flush()

    cases = list(
        seeded.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )
    bundle = evaluations.build_case_dispatches(
        seeded,
        run=run,
        case_runs=cases,
        attempt_no=2,
        shard_size=8,
        shard_prefix="labels",
        settings=get_settings(),
        now=datetime.now(UTC),
    )

    assert bundle.outbox_rows
    for row in bundle.outbox_rows:
        client_payload = row.payload_json["client_payload"]
        manifest = json.loads(row.payload_json["manifest_text"])
        validate_payload("dispatch", client_payload)
        validate_payload("manifest", manifest)
        assert client_payload["runs_on"] == ["self-hosted", "gpu", "eval"]
        assert manifest["runs_on"] == ["self-hosted", "gpu", "eval"]


def test_dispatch_ignores_legacy_fixed_pool_marker(
    seeded: Session, configure_settings
) -> None:
    """A legacy ``{"kind": "fixed"}`` marker must not become the execute label."""

    configure_settings()
    run, _ = create_eval(seeded, shard_size=8, idempotency_key="legacy-fixed-labels")
    pool = ResourcePool(
        id="pool-legacy-fixed",
        tenant_id=str(DEFAULT_TENANT_ID),
        project_id=str(DEFAULT_PROJECT_ID),
        name="legacy-fixed",
        provider_key="fixed",
        provisioning_mode="fixed",
        labels_json={"kind": "fixed"},
    )
    seeded.add(pool)
    seeded.flush()
    run.resource_pool_id = pool.id
    seeded.flush()

    cases = list(
        seeded.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )
    bundle = evaluations.build_case_dispatches(
        seeded,
        run=run,
        case_runs=cases,
        attempt_no=2,
        shard_size=8,
        shard_prefix="legacy-fixed",
        settings=get_settings(),
        now=datetime.now(UTC),
    )

    assert bundle.outbox_rows
    for row in bundle.outbox_rows:
        client_payload = row.payload_json["client_payload"]
        manifest = json.loads(row.payload_json["manifest_text"])
        validate_payload("dispatch", client_payload)
        validate_payload("manifest", manifest)
        assert client_payload["runs_on"] == ["self-hosted", "linux", "eval"]
        assert manifest["runs_on"] == ["self-hosted", "linux", "eval"]


def test_create_is_idempotent_on_client_key(seeded: Session, configure_settings) -> None:
    configure_settings()
    first, created_first = create_eval(seeded, idempotency_key="same-key")
    second, created_second = create_eval(seeded, idempotency_key="same-key")

    assert created_first is True
    assert created_second is False
    assert second.id == first.id
    assert seeded.query(Outbox).count() == 1
    assert seeded.query(EvalCaseRun).count() == len(CASE_KEYS)


@pytest.mark.parametrize(
    "payload",
    [
        {"case_keys": []},
        {"case_keys": ["ok", ""]},
        {"case_keys": [123]},
        {"case_keys": ["x"] * 1001},
        {"shard_size": 0},
        {"shard_size": 300},
    ],
)
def test_api_rejects_invalid_create_payload(seeded: Session, client: TestClient, payload) -> None:
    response = client.post("/api/v1/evaluations", json={"name": "x", **payload})
    assert response.status_code == 422


def test_api_create_returns_run_summary(seeded: Session, client: TestClient) -> None:
    response = client.post(
        "/api/v1/evaluations",
        json={"name": "冒烟", "case_keys": CASE_KEYS, "shard_size": 2, "idempotency_key": "api-1"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["total_cases"] == 3
    assert body["queued_count"] == 3
    assert body["name"] == "冒烟"

    replay = client.post(
        "/api/v1/evaluations",
        json={"name": "冒烟", "case_keys": CASE_KEYS, "shard_size": 2, "idempotency_key": "api-1"},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]


# ---------------------------------------------------------------------------
# Queries: list / detail / events cursor
# ---------------------------------------------------------------------------


def test_api_lists_evaluations_with_counts(seeded: Session, client: TestClient) -> None:
    client.post("/api/v1/evaluations", json={"case_keys": ["a", "b"], "idempotency_key": "l1"})
    client.post("/api/v1/evaluations", json={"case_keys": ["c"], "idempotency_key": "l2"})

    body = client.get("/api/v1/evaluations").json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    statuses = {item["status"] for item in body["items"]}
    assert statuses == {"queued"}
    assert body["stats"]["total"] == 2
    assert body["stats"]["active"] == 2
    assert body["stats"]["avg_resolve_rate"] is None


def test_api_list_filters_and_searches_on_server(seeded: Session, client: TestClient) -> None:
    """M5-16: q/status/dataset filters and pagination run in the query."""

    for index, name in enumerate(("Codex × SWE-bench", "Claude 合成", "Claude 合成")):
        created = client.post(
            "/api/v1/evaluations",
            json={"name": name, "case_keys": [f"case-{index}"], "idempotency_key": f"filter-{index}"},
        )
        assert created.status_code == 201

    by_name = client.get("/api/v1/evaluations", params={"q": "codex"}).json()
    assert by_name["total"] == 1
    assert by_name["items"][0]["name"] == "Codex × SWE-bench"
    assert by_name["stats"]["total"] == 1

    by_agent = client.get("/api/v1/evaluations", params={"q": "claude"}).json()
    assert by_agent["total"] == 2
    assert by_agent["stats"]["total"] == 2

    empty = client.get("/api/v1/evaluations", params={"q": "missing"}).json()
    assert empty["total"] == 0
    assert empty["items"] == []
    assert empty["stats"]["total"] == 0

    queued = client.get("/api/v1/evaluations", params={"status": "queued"}).json()
    assert queued["total"] == 3
    assert {item["status"] for item in queued["items"]} == {"queued"}

    completed = client.get("/api/v1/evaluations", params={"status": "completed"}).json()
    assert completed["total"] == 0

    paged = client.get("/api/v1/evaluations", params={"limit": 1, "offset": 1}).json()
    assert paged["total"] == 3
    assert len(paged["items"]) == 1
    assert paged["items"][0]["id"] in {
        item["id"] for item in client.get("/api/v1/evaluations").json()["items"]
    }

    invalid = client.get("/api/v1/evaluations", params={"status": "nope"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["error"]["code"] == "evaluation_invalid"


def test_api_list_exposes_dataset_agent_stats_and_resolve_rate(
    seeded: Session, client: TestClient
) -> None:
    """M5-16: list columns and stat cards are server aggregates, partial included."""

    tenant, _workspace, project = ensure_default_scope(seeded)
    dataset = Dataset(
        tenant_id=tenant.id,
        project_id=project.id,
        slug="swe-bench-verified",
        name="SWE-bench Verified",
        adapter_key="swebench",
    )
    seeded.add(dataset)
    seeded.flush()
    version = DatasetVersion(
        tenant_id=tenant.id,
        dataset_id=dataset.id,
        version="v2026.10",
        source_uri="fixture://swebench",
        source_revision="abcdef123456",
        adapter_version="1.0",
        case_count=2,
        status="active",
    )
    seeded.add(version)
    seeded.flush()

    now = datetime.now(UTC)
    partial = EvalRun(
        tenant_id=tenant.id,
        project_id=project.id,
        name="Codex × SWE-bench Verified",
        status="partial",
        dataset_version_id=version.id,
        runtime_key="codex",
        model_name="deepseek-v4.1",
        idempotency_key="stats-partial",
        created_by="tester",
        total_cases=2,
        succeeded_count=1,
        failed_count=1,
        started_at=now - timedelta(hours=4),
        finished_at=now - timedelta(hours=2),
    )
    running = EvalRun(
        tenant_id=tenant.id,
        project_id=project.id,
        name="Claude 实时评测",
        status="running",
        runtime_key="claude-code",
        model_name="glm-5",
        idempotency_key="stats-running",
        created_by="tester",
        total_cases=2,
        running_count=2,
        started_at=now - timedelta(minutes=30),
    )
    seeded.add_all([partial, running])
    seeded.flush()
    for ordinal, (case_key, result_status) in enumerate(
        (("case-a", "resolved"), ("case-b", "unresolved")), start=1
    ):
        seeded.add(
            EvalCaseRun(
                tenant_id=tenant.id,
                eval_run_id=partial.id,
                dataset_case_id=f"{version.id}-{case_key}",
                case_key=case_key,
                ordinal=ordinal,
                execution_status="succeeded",
                result_status=result_status,
            )
        )
    for ordinal, case_key in enumerate(("case-c", "case-d"), start=1):
        seeded.add(
            EvalCaseRun(
                tenant_id=tenant.id,
                eval_run_id=running.id,
                dataset_case_id=f"{version.id}-{case_key}",
                case_key=case_key,
                ordinal=ordinal,
                execution_status="running",
                result_status="not_scored",
            )
        )
    seeded.commit()

    body = client.get("/api/v1/evaluations").json()
    assert body["total"] == 2
    by_id = {item["id"]: item for item in body["items"]}

    partial_item = by_id[partial.id]
    assert partial_item["status"] == "partial"
    assert partial_item["dataset_name"] == "SWE-bench Verified"
    assert partial_item["dataset_version"] == "v2026.10"
    assert partial_item["runtime_key"] == "codex"
    assert partial_item["model_name"] == "deepseek-v4.1"
    assert partial_item["resolved_count"] == 1
    assert partial_item["scored_count"] == 2
    assert partial_item["resolve_rate"] == 50.0
    assert partial_item["duration_seconds"] == 7200

    running_item = by_id[running.id]
    assert running_item["resolve_rate"] is None
    assert running_item["duration_seconds"] >= 1700

    assert body["stats"] == {
        "total": 2,
        "active": 1,
        "running_cases": 2,
        "resolved_cases": 1,
        "scored_cases": 2,
        "avg_resolve_rate": 50.0,
        "avg_duration_seconds": 7200,
    }

    by_dataset = client.get("/api/v1/evaluations", params={"dataset_id": dataset.id}).json()
    assert by_dataset["total"] == 1
    assert by_dataset["items"][0]["id"] == partial.id
    assert by_dataset["stats"]["total"] == 1

    by_dataset_name = client.get("/api/v1/evaluations", params={"q": "Verified"}).json()
    assert by_dataset_name["total"] == 1

    by_model = client.get("/api/v1/evaluations", params={"q": "glm-5"}).json()
    assert by_model["total"] == 1
    assert by_model["items"][0]["id"] == running.id

    by_runtime = client.get("/api/v1/evaluations", params={"q": "claude-code"}).json()
    assert by_runtime["total"] == 1

    partial_only = client.get("/api/v1/evaluations", params={"status": "partial"}).json()
    assert partial_only["total"] == 1
    assert partial_only["items"][0]["resolve_rate"] == 50.0


def test_api_detail_includes_cases_and_shards(seeded: Session, client: TestClient) -> None:
    created = client.post(
        "/api/v1/evaluations", json={"case_keys": CASE_KEYS, "shard_size": 2, "idempotency_key": "d1"}
    ).json()

    body = client.get(f"/api/v1/evaluations/{created['id']}").json()
    assert body["id"] == created["id"]
    assert len(body["cases"]) == 3
    case = body["cases"][0]
    assert case["execution_status"] == "queued"
    assert case["latest_attempt_no"] == 1
    assert len(body["shards"]) == 2
    shard = body["shards"][0]
    assert shard["status"] == "pending"
    assert shard["dispatch_key"]
    assert shard["manifest_digest"].startswith("sha256:")

    assert client.get("/api/v1/evaluations/missing-run").status_code == 404


def test_api_events_cursor(seeded: Session, client: TestClient) -> None:
    created = client.post("/api/v1/evaluations", json={"case_keys": ["a"], "idempotency_key": "e1"}).json()
    run_id = created["id"]
    # extra events beyond eval.created
    for sequence in range(1, 4):
        seeded.add(
            EvalEvent(
                tenant_id=run_id and seeded.query(EvalRun).filter(EvalRun.id == run_id).one().tenant_id,
                eval_run_id=run_id,
                sequence=sequence,
                event_type="eval.note",
                payload_json={"n": sequence},
            )
        )
    seeded.commit()

    legacy = client.get(f"/api/v1/evaluations/{run_id}/events", params={"limit": 2}).json()
    # M5-05: no cursor opens the newest window so long runs show their tail.
    assert [item["sequence"] for item in legacy["items"]] == [2, 3]
    assert legacy["direction"] == "older"
    assert legacy["has_more"] is True

    older = client.get(
        f"/api/v1/evaluations/{run_id}/events",
        params={"before_id": legacy["next_cursor"], "limit": 2},
    ).json()
    assert [item["sequence"] for item in older["items"]] == [0, 1]
    assert older["next_cursor"] is None

    # after_id stays the forward/SSE replay contract.
    forward = client.get(
        f"/api/v1/evaluations/{run_id}/events",
        params={"after_id": older["items"][-1]["id"], "limit": 2},
    ).json()
    assert [item["sequence"] for item in forward["items"]] == [2, 3]
    assert forward["direction"] == "newer"
    assert forward["next_cursor"] is None


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_closes_pending_dispatch_and_attempts(seeded: Session, client: TestClient) -> None:
    created = client.post("/api/v1/evaluations", json={"case_keys": CASE_KEYS, "idempotency_key": "c1"}).json()
    run_id = created["id"]

    response = client.post(f"/api/v1/evaluations/{run_id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"

    run = seeded.query(EvalRun).filter(EvalRun.id == run_id).one()
    assert run.status == "cancelled"
    assert seeded.query(EvalCaseAttempt).filter(EvalCaseAttempt.status != "cancelled").count() == 0
    for row in seeded.query(Outbox).all():
        assert row.status == "cancelled"
        assert row.payload_json.get("last_error") == "run_cancelled"

    again = client.post(f"/api/v1/evaluations/{run_id}/cancel")
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"

    events = (
        seeded.query(EvalEvent)
        .filter(EvalEvent.eval_run_id == run_id, EvalEvent.event_type == "eval.cancelled")
        .all()
    )
    assert len(events) == 1


def test_cancel_terminal_run_is_rejected(seeded: Session, client: TestClient) -> None:
    created = client.post("/api/v1/evaluations", json={"case_keys": ["a"], "idempotency_key": "c2"}).json()
    run = seeded.query(EvalRun).filter(EvalRun.id == created["id"]).one()
    run.status = "failed"
    seeded.commit()

    response = client.post(f"/api/v1/evaluations/{created['id']}/cancel")
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "evaluation_already_final"


def test_outbox_cancelled_status_is_persisted(seeded: Session) -> None:
    """The outbox CHECK gains 'cancelled' via migration for cancellation bookkeeping."""

    # The service marks cancelled outbox rows; make sure the column constraint
    # accepts the value on this migrated schema.
    run, _ = create_eval(seeded, idempotency_key="ob1")
    evaluations.cancel_evaluation(seeded, run_id=run.id)
    statuses = {row.status for row in seeded.query(Outbox).all()}
    assert statuses == {"cancelled"}


def test_attempt_statuses_cover_new_loop_states(seeded: Session) -> None:
    """dispatch_failed / artifact_missing are pre-existing CHECK members."""

    for value in ("dispatch_failed", "artifact_missing", "queued", "running"):
        assert value in ATTEMPT_STATUSES

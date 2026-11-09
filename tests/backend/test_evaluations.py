"""M2-05 minimal synthetic evaluation CRUD + transactional shard outbox.

Creating an evaluation writes run/case/attempt/outbox rows in ONE transaction:
the outbox rows already carry the contract-valid repository_dispatch payload
and the byte-stable manifest text, so a crash right after commit can be
reconciled without regenerating anything (验收 2：事务 outbox 生成分片派发).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from app.config import get_settings
from app.models import (
    ATTEMPT_STATUSES,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    Outbox,
)
from app.runner_contracts import validate_payload
from app.seed import ensure_default_scope
from app.services import evaluations
from fastapi.testclient import TestClient
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
        assert client_payload["eval_run_id"] == run.id
        assert client_payload["attempt"] == 1
        assert client_payload["dispatch_key"] == row.dispatch_key

        manifest_text = payload["manifest_text"]
        manifest = json.loads(manifest_text)
        validate_payload("manifest", manifest)
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

    first = client.get(f"/api/v1/evaluations/{run_id}/events", params={"limit": 2}).json()
    assert [item["sequence"] for item in first["items"]] == [0, 1]
    assert first["next_cursor"] == first["items"][-1]["id"]

    second = client.get(
        f"/api/v1/evaluations/{run_id}/events", params={"after_id": first["next_cursor"], "limit": 2}
    ).json()
    assert [item["sequence"] for item in second["items"]] == [2, 3]
    assert second["next_cursor"] is None


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

"""M4-01 concurrency is a first-class create/prepare/detail field."""

from __future__ import annotations

import pytest
from app.models import EvalRun
from app.seed import ensure_default_scope
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

CASE_KEYS = ["case-a", "case-b"]


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def test_create_accepts_requested_concurrency_and_persists(
    seeded: Session, client: TestClient
) -> None:
    response = client.post(
        "/api/v1/evaluations",
        json={
            "name": "并发",
            "case_keys": CASE_KEYS,
            "requested_concurrency": 4,
            "idempotency_key": "conc-1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["requested_concurrency"] == 4
    assert body["effective_concurrency"] is None
    assert body["queue_reason"] is None

    run = seeded.query(EvalRun).filter(EvalRun.id == body["id"]).one()
    assert run.requested_concurrency == 4


def test_create_defaults_to_configured_concurrency(
    seeded: Session, client: TestClient, configure_settings
) -> None:
    configure_settings(EVAL_DEFAULT_REQUESTED_CONCURRENCY="3")
    response = client.post(
        "/api/v1/evaluations",
        json={"name": "默认并发", "case_keys": CASE_KEYS, "idempotency_key": "conc-default"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["requested_concurrency"] == 3


def test_idempotent_replay_does_not_change_concurrency(
    seeded: Session, client: TestClient
) -> None:
    first = client.post(
        "/api/v1/evaluations",
        json={
            "name": "幂等",
            "case_keys": CASE_KEYS,
            "requested_concurrency": 4,
            "idempotency_key": "conc-replay",
        },
    )
    assert first.status_code == 201

    replay = client.post(
        "/api/v1/evaluations",
        json={
            "name": "幂等",
            "case_keys": CASE_KEYS,
            "requested_concurrency": 2,
            "idempotency_key": "conc-replay",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["requested_concurrency"] == 4


def test_create_rejects_out_of_range_values(
    seeded: Session, client: TestClient
) -> None:
    for requested in (0, 257):
        response = client.post(
            "/api/v1/evaluations",
            json={"name": "越界", "case_keys": CASE_KEYS, "requested_concurrency": requested},
        )
        assert response.status_code == 422, requested

    too_large_shard = client.post(
        "/api/v1/evaluations",
        json={"name": "分片越界", "case_keys": CASE_KEYS, "shard_size": 256},
    )
    assert too_large_shard.status_code == 422


def test_detail_exposes_concurrency_and_queue_fields(
    seeded: Session, client: TestClient
) -> None:
    created = client.post(
        "/api/v1/evaluations",
        json={
            "name": "详情",
            "case_keys": CASE_KEYS,
            "requested_concurrency": 2,
            "idempotency_key": "conc-detail",
        },
    )
    assert created.status_code == 201
    detail = client.get(f"/api/v1/evaluations/{created.json()['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["requested_concurrency"] == 2
    assert "effective_concurrency" in body
    assert "queue_reason" in body

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.models import Runner
from app.worker.main import mark_stale_runners
from sqlalchemy import select
from sqlalchemy.orm import Session

TOKEN = "test-runner-token"


@pytest.fixture
def runner_token(monkeypatch):
    monkeypatch.setenv("RUNNER_HEARTBEAT_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield TOKEN
    monkeypatch.delenv("RUNNER_HEARTBEAT_TOKEN", raising=False)
    get_settings.cache_clear()


def post_heartbeat(client, token: str = TOKEN, **overrides):
    payload = {
        "runner_id": "runner-1",
        "name": "fixed-linux",
        "labels": ["self-hosted", "linux", "eval"],
        "status": "online",
        "current_case_attempt_id": None,
    }
    payload.update(overrides)
    return client.post(
        "/runner/v1/runners/heartbeat",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def test_heartbeat_rejects_wrong_token(client, runner_token) -> None:
    response = post_heartbeat(client, token="wrong")
    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "runner_token_invalid"


def test_heartbeat_registers_and_lists_runner(client, runner_token) -> None:
    response = post_heartbeat(client)
    assert response.status_code == 200, response.text

    listing = client.get("/api/v1/runners").json()
    assert listing["counts"] == {"online": 1, "total": 1}
    item = listing["items"][0]
    assert item["name"] == "fixed-linux"
    assert item["status"] == "online"
    assert item["labels"] == ["self-hosted", "linux", "eval"]
    assert item["last_heartbeat_at"] is not None
    assert item["current_case_attempt_id"] is None


def test_heartbeat_updates_busy_and_current_attempt(client, runner_token) -> None:
    assert post_heartbeat(client).status_code == 200
    response = post_heartbeat(
        client, status="busy", current_case_attempt_id="attempt-1"
    )
    assert response.status_code == 200

    item = client.get("/api/v1/runners").json()["items"][0]
    assert item["status"] == "busy"
    assert item["current_case_attempt_id"] == "attempt-1"


def test_stale_heartbeat_lists_offline_and_worker_marks_it(client, runner_token, engine) -> None:
    assert post_heartbeat(client).status_code == 200
    with Session(engine) as session:
        runner = session.scalar(select(Runner))
        assert runner is not None
        runner.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=600)
        session.commit()

    item = client.get("/api/v1/runners").json()["items"][0]
    assert item["status"] == "offline"

    with Session(engine) as session:
        assert mark_stale_runners(session, 90) == 1
        runner = session.scalar(select(Runner))
        assert runner is not None
        assert runner.status == "offline"

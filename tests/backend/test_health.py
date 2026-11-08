from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_is_liveness_only(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_checks_database(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["checks"]["database"] == "ok"
    assert response.json()["checks"]["redis"] == "disabled"


def test_overview_returns_default_scope_and_zeroes(client: TestClient) -> None:
    response = client.get("/api/v1/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["tenant"] == {"slug": "default", "name": "默认租户"}
    assert payload["project"] == {"slug": "default", "name": "默认项目"}
    assert payload["counts"] == {
        "eval_runs": 0,
        "running": 0,
        "queued": 0,
        "failed": 0,
        "runners_online": 0,
        "runners_total": 0,
    }

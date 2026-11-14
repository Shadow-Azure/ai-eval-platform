from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_returns_deploy_defaults(client: TestClient, configure_settings) -> None:
    configure_settings(DATASET_IMAGE_REGISTRIES="docker.io,docker.1panel.live")
    response = client.get("/api/v1/settings/dataset-image-sources")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["using_override"] is False
    assert [item["registry"] for item in body["effective"]] == [
        "registry-1.docker.io",
        "docker.1panel.live",
    ]
    assert all(item["is_default"] for item in body["effective"])


def test_put_saves_ordered_override_and_delete_resets(
    client: TestClient, configure_settings
) -> None:
    configure_settings(DATASET_IMAGE_REGISTRIES="docker.io")
    payload = {
        "sources": [
            {
                "name": "1Panel",
                "registry": "docker.1panel.live",
                "prefix": "",
                "enabled": True,
            },
            {
                "name": "Docker Hub",
                "registry": "docker.io",
                "prefix": "",
                "enabled": False,
            },
        ]
    }
    saved = client.put("/api/v1/settings/dataset-image-sources", json=payload)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["using_override"] is True
    assert body["updated_by"]
    assert [item["registry"] for item in body["effective"]] == [
        "docker.1panel.live",
        "registry-1.docker.io",
    ]
    assert body["effective"][0]["enabled"] is True
    assert body["effective"][1]["enabled"] is False

    fetched = client.get("/api/v1/settings/dataset-image-sources").json()
    assert fetched["using_override"] is True
    assert fetched["effective"][0]["name"] == "1Panel"

    reset = client.delete("/api/v1/settings/dataset-image-sources")
    assert reset.status_code == 200, reset.text
    assert reset.json()["using_override"] is False
    assert reset.json()["effective"][0]["registry"] == "registry-1.docker.io"


def test_put_rejects_duplicate_or_empty_sources(client: TestClient) -> None:
    duplicate = {
        "sources": [
            {"name": "A", "registry": "docker.io", "enabled": True},
            {"name": "B", "registry": "docker.io", "enabled": True},
        ]
    }
    response = client.put("/api/v1/settings/dataset-image-sources", json=duplicate)
    assert response.status_code == 422

    empty = {"sources": []}
    response = client.put("/api/v1/settings/dataset-image-sources", json=empty)
    assert response.status_code == 422

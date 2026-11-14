from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services import dataset_images


def test_swebench_image_derives_from_instance_id() -> None:
    assert (
        dataset_images.image_for_case(
            "swebench",
            "astropy__astropy-12907",
            {"instance_id": "astropy__astropy-12907"},
        )
        == "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
    )
    assert dataset_images.image_for_case("swebench", "not-an-instance") is None


def test_explicit_image_wins() -> None:
    raw = {"instance_id": "astropy__astropy-12907", "docker_image": "mirror.example/custom:1"}
    assert dataset_images.image_for_case("swebench", "astropy__astropy-12907", raw) == (
        "mirror.example/custom:1"
    )


def test_registry_source_parsing() -> None:
    docker = dataset_images.parse_registry_source("https://docker.io/")
    assert docker is not None
    assert docker.registry == "registry-1.docker.io"
    assert docker.prefix == ""
    mirror = dataset_images.parse_registry_source("docker.m.daocloud.io")
    assert mirror is not None
    assert mirror.registry == "docker.m.daocloud.io"
    ghcr = dataset_images.parse_registry_source("ghcr.io/acme")
    assert ghcr is not None
    assert ghcr.prefix == "acme"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_probe_case_images_passes_on_first_source() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"{}")

    settings = Settings(
        dataset_image_registries="mirror.example",
        dataset_image_probe_timeout_seconds=2,
        dataset_image_probe_concurrency=2,
    )
    check = dataset_images.probe_case_images(
        {"astropy__astropy-12907": "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"},
        settings=settings,
        client=_client(handler),
    )
    assert check["status"] == dataset_images.PASSED
    assert check["available"] == 1
    assert check["unavailable"] == []
    assert check["sources"][0]["status"] == dataset_images.FOUND
    assert "/v2/swebench/sweb.eval.x86_64.astropy_1776_astropy-12907/manifests/latest" in seen[0]


def test_probe_case_images_blocks_when_all_sources_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    settings = Settings(
        dataset_image_registries="mirror.example",
        dataset_image_probe_timeout_seconds=2,
    )
    check = dataset_images.probe_case_images(
        {"astropy__astropy-12907": "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"},
        settings=settings,
        client=_client(handler),
    )
    assert check["status"] == dataset_images.BLOCKED
    assert check["available"] == 0
    assert check["unavailable"][0]["case_key"] == "astropy__astropy-12907"
    assert check["sources"][0]["status"] == dataset_images.NOT_FOUND


def test_probe_case_images_skips_without_sources() -> None:
    settings = Settings(dataset_image_registries="")
    check = dataset_images.probe_case_images(
        {"astropy__astropy-12907": "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"},
        settings=settings,
    )
    assert check["status"] == dataset_images.SKIPPED
    assert check["checked"] == 1


def test_probe_source_auth_required_is_not_treated_as_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.docker.io":
            return httpx.Response(200, json={"token": "test-token"})
        return httpx.Response(401)

    result = dataset_images.probe_source(
        "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest",
        dataset_images.parse_registry_source("docker.io"),  # type: ignore[arg-type]
        timeout=2,
        client=_client(handler),
    )
    assert result.status == dataset_images.AUTH_REQUIRED
    assert result.http_status == 401

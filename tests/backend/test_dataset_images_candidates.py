"""Issue #290: ordered pull candidates for one dataset image reference.

The prepare worker may pull an image from the ordered public sources, but the
canonical reference (the one the runner and ``docker save`` use) never depends
on which mirror served it. These tests pin the candidate ordering, the Docker
Hub canonical form and digest preservation.
"""

from __future__ import annotations

from app.services.dataset_images import RegistrySource, candidate_ref_pairs, candidate_refs

HUB = RegistrySource(name="Docker Hub", registry="registry-1.docker.io")
GHCR = RegistrySource(name="ghcr.io/acme", registry="ghcr.io", prefix="acme")
MIRROR = RegistrySource(name="docker.m.daocloud.io", registry="docker.m.daocloud.io")
DIGEST = "sha256:" + "a" * 64


def test_docker_hub_candidate_is_the_canonical_reference() -> None:
    assert candidate_refs("swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest", [HUB]) == [
        "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
    ]
    # An explicit Docker Hub host is normalized away so docker pull keeps using
    # the default registry instead of a mirror/proxy host.
    assert candidate_refs("docker.io/library/alpine:3.20", [HUB]) == ["library/alpine:3.20"]


def test_missing_tag_defaults_to_latest() -> None:
    assert candidate_refs("library/alpine", [HUB]) == ["library/alpine:latest"]


def test_sources_are_ordered_and_mirrors_keep_registry_and_prefix() -> None:
    assert candidate_refs("swebench/example:1.0", [HUB, GHCR, MIRROR]) == [
        "swebench/example:1.0",
        "ghcr.io/acme/swebench/example:1.0",
        "docker.m.daocloud.io/swebench/example:1.0",
    ]


def test_explicit_registry_host_is_replaced_by_the_source() -> None:
    assert candidate_refs("registry.example.com/team/image:2", [GHCR]) == [
        "ghcr.io/acme/team/image:2"
    ]


def test_digest_reference_is_preserved_for_every_candidate() -> None:
    image = f"swebench/example@{DIGEST}"
    assert candidate_refs(image, [HUB, GHCR]) == [
        f"swebench/example@{DIGEST}",
        f"ghcr.io/acme/swebench/example@{DIGEST}",
    ]


def test_disabled_and_duplicate_sources_are_skipped() -> None:
    duplicate_hub = RegistrySource(name="hub-copy", registry="docker.io")
    disabled = RegistrySource(name="disabled", registry="ghcr.io", prefix="acme", enabled=False)
    assert candidate_refs("swebench/example:1.0", [HUB, duplicate_hub, disabled]) == [
        "swebench/example:1.0"
    ]


def test_empty_source_list_falls_back_to_the_canonical_reference() -> None:
    assert candidate_refs("swebench/example:1.0", []) == ["swebench/example:1.0"]
    assert candidate_refs("", [HUB]) == []


def test_pairs_keep_the_source_that_produced_each_candidate() -> None:
    pairs = candidate_ref_pairs("swebench/example:1.0", [HUB, GHCR])
    assert [(source.name, ref) for source, ref in pairs] == [
        ("Docker Hub", "swebench/example:1.0"),
        ("ghcr.io/acme", "ghcr.io/acme/swebench/example:1.0"),
    ]


def test_source_health_ordering_prefers_fastest_found_source(monkeypatch) -> None:
    import app.services.dataset_images as dataset_images

    sources = [HUB, GHCR, MIRROR]
    probes = {
        HUB.name: dataset_images.SourceProbe(source=HUB, status=dataset_images.NOT_FOUND),
        GHCR.name: dataset_images.SourceProbe(
            source=GHCR, status=dataset_images.FOUND, elapsed_ms=120
        ),
        MIRROR.name: dataset_images.SourceProbe(
            source=MIRROR, status=dataset_images.FOUND, elapsed_ms=40
        ),
    }
    monkeypatch.setattr(
        dataset_images,
        "probe_source",
        lambda _image, source, **_kwargs: probes[source.name],
    )

    ordering = dataset_images.order_sources_by_health(
        "swebench/example:1.0", sources, timeout=1.0
    )

    assert [source.name for source in ordering.sources] == ["docker.m.daocloud.io", "ghcr.io/acme"]
    assert ordering.skipped == ["Docker Hub"]
    assert ordering.fallback is False


def test_source_health_ordering_keeps_transient_errors_as_fallbacks(monkeypatch) -> None:
    import app.services.dataset_images as dataset_images

    sources = [HUB, GHCR, MIRROR]
    probes = {
        HUB.name: dataset_images.SourceProbe(
            source=HUB, status=dataset_images.ERROR, error="ConnectError"
        ),
        GHCR.name: dataset_images.SourceProbe(source=GHCR, status=dataset_images.AUTH_REQUIRED),
        MIRROR.name: dataset_images.SourceProbe(
            source=MIRROR, status=dataset_images.FOUND, elapsed_ms=80
        ),
    }
    monkeypatch.setattr(
        dataset_images,
        "probe_source",
        lambda _image, source, **_kwargs: probes[source.name],
    )

    ordering = dataset_images.order_sources_by_health(
        "swebench/example:1.0", sources, timeout=1.0
    )

    # A found source is first; a transient probe failure remains a fallback
    # because the manifest probe may be wrong, but a 401 source is skipped.
    assert [source.name for source in ordering.sources] == ["docker.m.daocloud.io", "Docker Hub"]
    assert ordering.skipped == ["ghcr.io/acme"]


def test_source_health_ordering_falls_back_when_every_source_is_unusable(monkeypatch) -> None:
    import app.services.dataset_images as dataset_images

    probes = {
        HUB.name: dataset_images.SourceProbe(source=HUB, status=dataset_images.NOT_FOUND),
        GHCR.name: dataset_images.SourceProbe(source=GHCR, status=dataset_images.RATE_LIMITED),
    }
    monkeypatch.setattr(
        dataset_images,
        "probe_source",
        lambda _image, source, **_kwargs: probes[source.name],
    )

    ordering = dataset_images.order_sources_by_health(
        "swebench/example:1.0", [HUB, GHCR], timeout=1.0
    )

    assert [source.name for source in ordering.sources] == ["Docker Hub", "ghcr.io/acme"]
    assert ordering.skipped == ["Docker Hub", "ghcr.io/acme"]
    assert ordering.fallback is True


def test_source_health_ordering_prefers_measured_throughput_over_ttfb(monkeypatch) -> None:
    import app.services.dataset_images as dataset_images

    slow_but_fast_ttfb = dataset_images.SourceProbe(
        source=HUB, status=dataset_images.FOUND, elapsed_ms=5
    )
    fast_but_slow_ttfb = dataset_images.SourceProbe(
        source=GHCR, status=dataset_images.FOUND, elapsed_ms=200
    )
    monkeypatch.setattr(
        dataset_images,
        "probe_source",
        lambda _image, source, **_kwargs: (
            slow_but_fast_ttfb if source.name == HUB.name else fast_but_slow_ttfb
        ),
    )
    stats = {
        HUB.name: {
            "successes": 1,
            "failures": 0,
            "total_seconds": 100.0,
            "total_bytes": 10_000_000,
        },
        GHCR.name: {
            "successes": 1,
            "failures": 0,
            "total_seconds": 10.0,
            "total_bytes": 100_000_000,
        },
    }

    ordering = dataset_images.order_sources_by_health(
        "swebench/example:1.0", [HUB, GHCR], timeout=1.0, stats=stats
    )

    assert [source.name for source in ordering.sources] == [GHCR.name, HUB.name]


def test_source_stats_record_success_rate_and_bytes() -> None:
    import app.services.dataset_images as dataset_images

    dataset_images.reset_source_stats()
    try:
        dataset_images.record_source_result(
            "mirror", seconds=2.0, bytes_transferred=4_000_000, success=True
        )
        dataset_images.record_source_result(
            "mirror", seconds=1.0, bytes_transferred=0, success=False
        )
        snapshot = dataset_images.source_stats_snapshot()["mirror"]
        assert snapshot["successes"] == 1
        assert snapshot["failures"] == 1
        assert snapshot["total_seconds"] == 2.0
        assert snapshot["total_bytes"] == 4_000_000
    finally:
        dataset_images.reset_source_stats()

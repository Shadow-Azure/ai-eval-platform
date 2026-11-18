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

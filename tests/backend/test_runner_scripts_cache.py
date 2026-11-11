"""M5-12: content-addressed runner scripts cache in OSS."""

from __future__ import annotations

import pytest
from app.services.object_store import ObjectMissing, ObjectStoreError
from app.services.runner_scripts_cache import (
    MIN_RUNNER_SCRIPTS_BYTES,
    RUNNER_SCRIPTS_ROUTE,
    ensure_runner_scripts_bundle,
    runner_scripts_key,
    runner_scripts_path,
    runner_scripts_url,
    valid_runner_scripts_name,
)

NAME = "eval-runner-scripts-0123456789abcdef0123456789abcdef.tar.gz"
VALID = b"\x1f\x8b" + b"x" * MIN_RUNNER_SCRIPTS_BYTES


class FakeStore:
    def __init__(self, sizes: dict[str, int] | None = None) -> None:
        self.sizes = sizes or {}
        self.puts: list[tuple[str, bytes, str]] = []

    def head_object(self, key: str) -> int:
        if key not in self.sizes:
            raise ObjectMissing(key)
        return self.sizes[key]

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self.puts.append((key, data, content_type))
        self.sizes[key] = len(data)


def test_name_is_the_content_address() -> None:
    assert valid_runner_scripts_name(NAME)
    assert not valid_runner_scripts_name("eval-runner-scripts-latest.tar.gz")
    assert runner_scripts_key(NAME) == f"runner/scripts/{NAME}"
    assert runner_scripts_path(NAME) == f"{RUNNER_SCRIPTS_ROUTE}{NAME}"
    assert runner_scripts_url("http://10.0.0.5:8000/", NAME) == (
        f"http://10.0.0.5:8000{RUNNER_SCRIPTS_ROUTE}{NAME}"
    )


def test_reuses_an_existing_same_size_bundle() -> None:
    store = FakeStore({runner_scripts_key(NAME): len(VALID)})
    assert ensure_runner_scripts_bundle(store, name=NAME, payload=VALID) is False
    assert store.puts == []


def test_uploads_when_missing() -> None:
    store = FakeStore()
    assert ensure_runner_scripts_bundle(store, name=NAME, payload=VALID) is True
    assert store.puts == [(runner_scripts_key(NAME), VALID, "application/gzip")]


def test_replaces_a_truncated_bundle() -> None:
    store = FakeStore({runner_scripts_key(NAME): 1024})
    assert ensure_runner_scripts_bundle(store, name=NAME, payload=VALID) is True
    assert store.puts[0][0] == runner_scripts_key(NAME)


def test_rejects_an_invalid_bundle_or_name() -> None:
    store = FakeStore()
    with pytest.raises(ObjectStoreError):
        ensure_runner_scripts_bundle(store, name=NAME, payload=b"not-a-gzip")
    with pytest.raises(ObjectStoreError):
        ensure_runner_scripts_bundle(store, name="latest.tar.gz", payload=VALID)
    assert store.puts == []


def test_build_is_deterministic_and_contains_the_schema_tree(tmp_path) -> None:
    import io
    import tarfile

    from app.services.runner_scripts_cache import BundleEntry, build_scripts_bundle

    entries = [
        BundleEntry("runner/lib/contracts.mjs", 0o644, b"export const x = 1;\n"),
        BundleEntry("runner/scripts/plan-cases.mjs", 0o755, b"#!/usr/bin/env node\n"),
        BundleEntry("runner-contract/schemas/v1/dispatch.schema.json", 0o644, b"{}\n"),
    ]
    first = build_scripts_bundle(tmp_path, entries=entries)
    second = build_scripts_bundle(tmp_path, entries=list(reversed(entries)))

    assert first.md5 == second.md5
    assert first.sha256 == second.sha256
    assert first.name == f"eval-runner-scripts-{first.md5}.tar.gz"

    with tarfile.open(fileobj=io.BytesIO(first.payload), mode="r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        assert list(members) == [
            "runner-contract/schemas/v1/dispatch.schema.json",
            "runner/lib/contracts.mjs",
            "runner/scripts/plan-cases.mjs",
        ]
        assert members["runner/scripts/plan-cases.mjs"].mode == 0o755
        assert members["runner/lib/contracts.mjs"].mode == 0o644
        assert all(member.mtime == 0 and member.uid == 0 and member.gid == 0 for member in members.values())
        assert archive.extractfile("runner/lib/contracts.mjs").read() == b"export const x = 1;\n"


def test_build_rejects_a_traversal_path(tmp_path) -> None:
    from app.services.runner_scripts_cache import BundleEntry, build_scripts_bundle

    with pytest.raises(ObjectStoreError):
        build_scripts_bundle(
            tmp_path,
            entries=[BundleEntry("../evil.mjs", 0o644, b"x")],
        )

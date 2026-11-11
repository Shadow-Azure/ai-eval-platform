"""Runner tarball cache (M5-08/M5-11): reuse OSS, upload only when absent/corrupt."""

from __future__ import annotations

import pytest
from app.services.object_store import ObjectMissing, ObjectStoreError
from app.services.runner_cache import (
    MIN_RUNNER_BYTES,
    RUNNER_TARBALL_ROUTE,
    ensure_runner_cache,
    github_runner_url,
    runner_cache_key,
    runner_tarball_path,
    runner_tarball_url,
)

VALID = b"\x1f\x8b" + b"x" * MIN_RUNNER_BYTES


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


def test_reuses_an_existing_archive_without_downloading() -> None:
    store = FakeStore({runner_cache_key("2.338.2"): MIN_RUNNER_BYTES + 1})
    calls: list[str] = []

    uploaded = ensure_runner_cache(
        store, version="2.338.2", downloader=lambda version: calls.append(version) or VALID
    )

    assert uploaded is False
    assert calls == []
    assert store.puts == []


def test_uploads_when_missing() -> None:
    store = FakeStore()
    uploaded = ensure_runner_cache(store, version="2.338.2", downloader=lambda _v: VALID)

    assert uploaded is True
    assert store.puts and store.puts[0][1] == VALID
    assert store.puts[0][2] == "application/gzip"


def test_replaces_a_truncated_cache_object() -> None:
    key = runner_cache_key("2.338.2")
    store = FakeStore({key: 1024})
    uploaded = ensure_runner_cache(store, version="2.338.2", downloader=lambda _v: VALID)

    assert uploaded is True
    assert store.puts[0][0] == key


def test_rejects_an_invalid_archive() -> None:
    store = FakeStore()
    with pytest.raises(ObjectStoreError):
        ensure_runner_cache(store, version="2.338.2", downloader=lambda _v: b"not-a-gzip")
    assert store.puts == []


def test_proxy_path_is_permanent_and_version_pinned() -> None:
    assert RUNNER_TARBALL_ROUTE == "/runner/v1/runner-tarball/"
    assert runner_tarball_path("2.338.2") == "/runner/v1/runner-tarball/2.338.2.tar.gz"
    assert runner_tarball_url("http://10.0.0.5:8000/", "2.338.2") == (
        "http://10.0.0.5:8000/runner/v1/runner-tarball/2.338.2.tar.gz"
    )


def test_github_url_is_version_pinned() -> None:
    assert github_runner_url("2.338.2") == (
        "https://github.com/actions/runner/releases/download/v2.338.2/"
        "actions-runner-linux-x64-2.338.2.tar.gz"
    )

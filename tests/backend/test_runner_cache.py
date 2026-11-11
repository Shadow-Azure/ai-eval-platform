"""Runner tarball cache (M5-08): reuse OSS, upload only when absent/corrupt."""

from __future__ import annotations

import pytest
from app.services.object_store import ObjectMissing, ObjectStoreError
from app.services.runner_cache import (
    MAX_PRESIGN_SECONDS,
    MIN_RUNNER_BYTES,
    ensure_runner_cache,
    github_runner_url,
    runner_cache_key,
)

VALID = b"\x1f\x8b" + b"x" * MIN_RUNNER_BYTES


class FakeStore:
    def __init__(self, sizes: dict[str, int] | None = None) -> None:
        self.sizes = sizes or {}
        self.puts: list[tuple[str, bytes, str]] = []
        self.presigned: list[tuple[str, int]] = []

    def head_object(self, key: str) -> int:
        if key not in self.sizes:
            raise ObjectMissing(key)
        return self.sizes[key]

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self.puts.append((key, data, content_type))
        self.sizes[key] = len(data)

    def presign_get_url(self, key: str, *, expires: int = 3600) -> str:
        self.presigned.append((key, expires))
        return f"https://oss.example/{key}?expires={expires}"


def test_reuses_an_existing_archive_without_downloading() -> None:
    store = FakeStore({runner_cache_key("2.338.2"): MIN_RUNNER_BYTES + 1})
    calls: list[str] = []

    url, uploaded = ensure_runner_cache(
        store, version="2.338.2", downloader=lambda version: calls.append(version) or VALID
    )

    assert uploaded is False
    assert calls == []
    assert store.puts == []
    assert url.startswith("https://oss.example/runner/actions-runner-linux-x64-2.338.2.tar.gz")
    assert store.presigned[0][1] == MAX_PRESIGN_SECONDS


def test_uploads_when_missing_and_returns_presigned_url() -> None:
    store = FakeStore()
    url, uploaded = ensure_runner_cache(store, version="2.338.2", downloader=lambda _v: VALID)

    assert uploaded is True
    assert store.puts and store.puts[0][1] == VALID
    assert store.puts[0][2] == "application/gzip"
    assert "actions-runner-linux-x64-2.338.2.tar.gz" in url


def test_replaces_a_truncated_cache_object() -> None:
    key = runner_cache_key("2.338.2")
    store = FakeStore({key: 1024})
    _, uploaded = ensure_runner_cache(store, version="2.338.2", downloader=lambda _v: VALID)

    assert uploaded is True
    assert store.puts[0][0] == key


def test_rejects_an_invalid_archive() -> None:
    store = FakeStore()
    with pytest.raises(ObjectStoreError):
        ensure_runner_cache(store, version="2.338.2", downloader=lambda _v: b"not-a-gzip")
    assert store.puts == []


def test_github_url_is_version_pinned() -> None:
    assert github_runner_url("2.338.2") == (
        "https://github.com/actions/runner/releases/download/v2.338.2/"
        "actions-runner-linux-x64-2.338.2.tar.gz"
    )

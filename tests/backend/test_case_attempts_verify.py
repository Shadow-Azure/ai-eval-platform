"""M6-01 (#90): object verification must not serialize 128 HEAD requests."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

from app.services import case_attempts


class _BarrierStore:
    def __init__(self) -> None:
        self.barrier = threading.Barrier(4, timeout=2)

    def verify_object(self, object_key: str, expected_size: int | None = None) -> None:
        self.barrier.wait()


def test_verify_objects_uses_bounded_concurrency(monkeypatch) -> None:
    store = _BarrierStore()
    monkeypatch.setattr(case_attempts, "get_settings", lambda: object())
    monkeypatch.setattr(case_attempts, "object_store_from_settings", lambda _settings: store)
    objects = [
        {
            "artifact_kind": "score",
            "object_key": f"objects/{index}",
            "sha256": "a" * 64,
            "size": 1,
        }
        for index in range(4)
    ]

    case_attempts._verify_objects(SimpleNamespace(), objects, now=datetime.now(UTC))

    assert store.barrier.n_waiting == 0

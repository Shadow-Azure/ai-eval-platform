"""Object storage port used by dataset packages and evaluation artifacts.

The local driver backs development and tests. Production swaps in an OSS
driver behind the same small interface; callers only pass platform-generated
keys and never let runners pick prefixes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ObjectStoreError(RuntimeError):
    """Raised for keys that escape the store or cannot be read safely."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int

    def as_dict(self) -> dict[str, object]:
        return {"object_key": self.key, "sha256": self.sha256, "size": self.size}


class ObjectStore(Protocol):
    def put_bytes(self, key: str, data: bytes) -> StoredObject: ...

    def put_text(self, key: str, text: str) -> StoredObject: ...

    def put_json(self, key: str, value: object) -> StoredObject: ...

    def read_bytes(self, key: str) -> bytes: ...

    def read_json(self, key: str) -> object: ...

    def exists(self, key: str) -> bool: ...


def normalize_key(key: str) -> str:
    """Normalize and validate a platform-generated object key."""

    if not isinstance(key, str) or not key.strip():
        raise ObjectStoreError("object key is empty")
    if key.startswith("/"):
        raise ObjectStoreError(f"object key must be relative: {key!r}")
    candidate = key.strip()
    parts = candidate.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ObjectStoreError(f"object key has an invalid segment: {key!r}")
    return "/".join(parts)


class LocalObjectStore:
    """Content-addressed filesystem store used for local development and tests."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.resolve()

    def resolve(self) -> Path:
        return self.root.resolve()

    def _path(self, key: str) -> Path:
        normalized = normalize_key(key)
        target = (self.root / normalized).resolve()
        if not target.is_relative_to(self.resolve()):
            raise ObjectStoreError(f"object key escapes the store root: {key!r}")
        return target

    def put_bytes(self, key: str, data: bytes) -> StoredObject:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return StoredObject(
            key=normalize_key(key),
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )

    def put_text(self, key: str, text: str) -> StoredObject:
        return self.put_bytes(key, text.encode("utf-8"))

    def put_json(self, key: str, value: object) -> StoredObject:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return self.put_text(key, payload)

    def read_bytes(self, key: str) -> bytes:
        target = self._path(key)
        if not target.is_file():
            raise ObjectStoreError(f"object not found: {key!r}")
        return target.read_bytes()

    def read_json(self, key: str) -> object:
        return json.loads(self.read_bytes(key).decode("utf-8"))

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

"""Native trace manifest validation and capture-status semantics (M6-01).

The runner may report one legacy single-file ``trace_manifest`` or a
platform-keyed multi-file bundle. This module is the single backend authority
for safe logical paths, duplicate-digest collapse, attempt-local manifests and
the ``trace_status`` derived from capture completeness. It never parses the
native files themselves; normalization arrives in later M6 work.
"""

from __future__ import annotations

import re
from typing import Any

MAX_LOGICAL_PATH = 512
MAX_MANIFEST_OBJECTS = 128

ARTIFACT_KINDS = ("score", "trace", "trace_metadata", "trace_diagnostic", "artifact")
TRACE_ARTIFACT_KINDS = ("trace", "trace_metadata", "trace_diagnostic")

SAFE_LOGICAL_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

CAPTURE_STATUSES = ("complete", "partial", "incomplete", "degraded", "failed")
TRACE_STATUS_BY_CAPTURE = {
    "complete": "ready",
    "partial": "incomplete",
    "incomplete": "incomplete",
    "degraded": "degraded",
    "failed": "failed",
}

OPTIONAL_STRING_FIELDS = {
    "native_format": 128,
    "runtime_version": 64,
    "format_version": 64,
    "adapter_version": 64,
}


class TraceManifestError(ValueError):
    def __init__(self, message: str, *, code: str = "manifest_invalid") -> None:
        self.code = code
        super().__init__(message)


def is_safe_logical_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > MAX_LOGICAL_PATH:
        return False
    if "\x00" in value or "\\" in value or value.startswith("/"):
        return False
    if SAFE_LOGICAL_PATH.fullmatch(value) is None:
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _require_string(raw: dict, key: str, *, maximum: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise TraceManifestError(f"{key} 必须是非空字符串（最长 {maximum}）")
    return value


def _optional_string(raw: dict, key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    maximum = OPTIONAL_STRING_FIELDS[key]
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise TraceManifestError(f"{key} 必须是非空字符串（最长 {maximum}）")
    return value


def _logical_path(raw: dict) -> str | None:
    candidates = [raw.get("logical_path"), raw.get("path")]
    present = [item for item in candidates if item is not None]
    if not present:
        return None
    if len(present) == 2 and present[0] != present[1]:
        raise TraceManifestError("logical_path 与 path 不一致")
    value = present[0]
    if not is_safe_logical_path(value):
        raise TraceManifestError(f"非法逻辑路径：{value}")
    return value


def normalize_object(raw: Any, *, allowed_kinds: tuple[str, ...]) -> dict:
    if not isinstance(raw, dict):
        raise TraceManifestError("manifest object 必须是对象")
    kind = raw.get("artifact_kind")
    if kind not in allowed_kinds:
        raise TraceManifestError(f"不支持的 artifact_kind：{kind}")
    object_key = _require_string(raw, "object_key", maximum=1024)
    digest = raw.get("sha256")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        raise TraceManifestError("sha256 必须是 64 位小写十六进制")
    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise TraceManifestError("size 必须是非负整数")
    content_type = _require_string(raw, "content_type", maximum=128)
    logical_path = _logical_path(raw)

    item = {
        "artifact_kind": kind,
        "object_key": object_key,
        "sha256": digest,
        "size": size,
        "content_type": content_type,
    }
    if logical_path is not None:
        item["logical_path"] = logical_path
    for key in ("native_format", "runtime_version", "format_version"):
        value = _optional_string(raw, key)
        if value is not None:
            item[key] = value
    return item


def _dedupe(objects: list[dict]) -> list[dict]:
    by_digest: dict[tuple[str, str], dict] = {}
    path_digest: dict[str, str] = {}
    for item in objects:
        logical_path = item.get("logical_path")
        if logical_path is not None:
            prior = path_digest.get(logical_path)
            if prior is not None and prior != item["sha256"]:
                raise TraceManifestError(
                    f"同一逻辑路径对应多个 digest：{logical_path}", code="manifest_conflict"
                )
            path_digest[logical_path] = item["sha256"]
        key = (item["artifact_kind"], item["sha256"])
        if key in by_digest:
            # Same bytes are one platform object; repeated reporting is
            # unambiguous and never creates a second manifest entry.
            continue
        by_digest[key] = item
    return list(by_digest.values())


def normalize_upload_objects(objects: Any) -> list[dict]:
    if not isinstance(objects, list) or not objects:
        raise TraceManifestError("objects 必须是非空数组")
    if len(objects) > MAX_MANIFEST_OBJECTS:
        raise TraceManifestError(f"objects 不能超过 {MAX_MANIFEST_OBJECTS} 项")
    return _dedupe([normalize_object(raw, allowed_kinds=ARTIFACT_KINDS) for raw in objects])


def normalize_trace_objects(objects: Any) -> list[dict]:
    if not isinstance(objects, list) or not objects:
        raise TraceManifestError("trace objects 必须是非空数组")
    if len(objects) > MAX_MANIFEST_OBJECTS:
        raise TraceManifestError(f"trace objects 不能超过 {MAX_MANIFEST_OBJECTS} 项")
    return _dedupe([normalize_object(raw, allowed_kinds=TRACE_ARTIFACT_KINDS) for raw in objects])


def _infer_capture_status(objects: list[dict]) -> str:
    kinds = {item["artifact_kind"] for item in objects}
    return "complete" if kinds & {"trace", "trace_metadata"} else "partial"


def normalize_trace_manifest(raw: Any) -> dict:
    """Return the persisted form: legacy single object or multi-file bundle."""

    if not isinstance(raw, dict):
        raise TraceManifestError("trace_manifest 必须是对象")
    if "objects" in raw:
        objects = normalize_trace_objects(raw["objects"])
        status = raw.get("capture_status")
        if status is None:
            status = _infer_capture_status(objects)
        if status not in CAPTURE_STATUSES:
            raise TraceManifestError(f"不支持的 capture_status：{status}")
        reasons = raw.get("incomplete_reasons", [])
        if not isinstance(reasons, list) or len(reasons) > 16:
            raise TraceManifestError("incomplete_reasons 必须是最多 16 项的数组")
        for reason in reasons:
            if not isinstance(reason, str) or not reason or len(reason) > 128:
                raise TraceManifestError("incomplete_reasons 每项必须是非空短字符串")
        # A runner may not claim a complete native capture from diagnostics
        # alone: stdout/stderr are fallbacks, not the authoritative session.
        if status == "complete" and not any(item["artifact_kind"] == "trace" for item in objects):
            status = "partial"
            if "native_session_missing" not in reasons:
                reasons = [*reasons, "native_session_missing"]
        record = {
            "manifest_version": str(raw.get("manifest_version") or "1.0"),
            "capture_status": status,
            "incomplete_reasons": list(reasons),
            "objects": objects,
        }
        for key in ("runtime_version", "format_version", "adapter_version"):
            value = _optional_string(raw, key)
            if value is not None:
                record[key] = value
        return record

    # Legacy v1 single-file manifest: three required fields, no logical path.
    object_key = _require_string(raw, "object_key", maximum=1024)
    digest = raw.get("sha256")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        raise TraceManifestError("sha256 必须是 64 位小写十六进制")
    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise TraceManifestError("size 必须是非负整数")
    return {"object_key": object_key, "sha256": digest, "size": size}


def trace_objects(manifest: dict | None) -> list[dict]:
    if not manifest:
        return []
    if isinstance(manifest.get("objects"), list):
        return list(manifest["objects"])
    if manifest.get("object_key"):
        return [
            {
                "artifact_kind": "trace",
                "object_key": manifest["object_key"],
                "sha256": manifest["sha256"],
                "size": manifest["size"],
                "content_type": "application/x-ndjson",
            }
        ]
    return []


def trace_status_for(manifest: dict | None) -> str:
    if not manifest:
        return "unlinked"
    if isinstance(manifest.get("objects"), list):
        return TRACE_STATUS_BY_CAPTURE[
            manifest.get("capture_status") or _infer_capture_status(manifest["objects"])
        ]
    if manifest.get("object_key"):
        return "ready"
    return "unlinked"


def merge_manifest_objects(existing: Any, incoming: list[dict]) -> list[dict]:
    """Merge by platform object key; one path can never map to two digests."""

    merged: list[dict] = []
    by_object_key: dict[str, dict] = {}
    path_digest: dict[str, str] = {}
    for raw in existing or []:
        if not isinstance(raw, dict) or not raw.get("object_key"):
            continue
        item = dict(raw)
        merged.append(item)
        by_object_key[item["object_key"]] = item
        if item.get("logical_path"):
            path_digest[item["logical_path"]] = item.get("sha256")

    for item in incoming:
        logical_path = item.get("logical_path")
        if logical_path:
            prior = path_digest.get(logical_path)
            if prior is not None and prior != item["sha256"]:
                raise TraceManifestError(
                    f"同一逻辑路径对应多个 digest：{logical_path}", code="manifest_conflict"
                )
            path_digest[logical_path] = item["sha256"]
        known = by_object_key.get(item["object_key"])
        if known is not None:
            if known.get("sha256") != item["sha256"]:
                raise TraceManifestError(
                    f"object_key 对应多个 digest：{item['object_key']}", code="manifest_conflict"
                )
            for key in ("logical_path", "native_format", "runtime_version", "format_version"):
                if item.get(key) is not None and not known.get(key):
                    known[key] = item[key]
            continue
        merged.append(item)
        by_object_key[item["object_key"]] = item
    return merged

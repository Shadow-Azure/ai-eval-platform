"""Python mirror of the runner-contract JSON Schema subset (M2-04).

`runner-contract/schemas/v1/*.schema.json` are the normative artifacts shared
by the platform API, the GitHub workflow and local fixtures. This module
validates payloads against those exact files, mirroring
`runner/lib/json-schema-subset.mjs` (same keyword subset, same semantics) so
the FastAPI plane stays dependency-free.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

KIND_FILES = {
    "dispatch": "dispatch.schema.json",
    "manifest": "task-manifest.schema.json",
    "started": "started.schema.json",
    "heartbeat": "heartbeat.schema.json",
    "upload": "upload-manifest.schema.json",
    "completion": "completion.schema.json",
    "failure": "failure.schema.json",
}


class ContractInvalid(Exception):
    """Payload does not satisfy the v1 contract schema for its kind."""

    def __init__(self, kind: str, errors: list[str]) -> None:
        self.kind = kind
        self.errors = errors
        super().__init__(f"{kind} 校验失败：{'; '.join(errors)}")


def schema_dir() -> Path:
    """Locate runner-contract/schemas/v1 (env override, repo root, cwd)."""

    candidates = []
    env_dir = os.environ.get("RUNNER_CONTRACT_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path(__file__).resolve().parents[2] / "runner-contract" / "schemas" / "v1")
    candidates.append(Path.cwd() / "runner-contract" / "schemas" / "v1")
    for candidate in candidates:
        if (candidate / "heartbeat.schema.json").is_file():
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"找不到 runner-contract/schemas/v1（搜索过：{searched}）")


@cache
def _cached_schema_dir() -> str:
    return str(schema_dir())


@cache
def load_schema(kind: str) -> dict[str, Any]:
    file = KIND_FILES.get(kind)
    if file is None:
        raise ValueError(f"未知契约类型：{kind}")
    with open(Path(_cached_schema_dir()) / file, encoding="utf-8") as handle:
        return json.load(handle)


def _is_plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def _type_matches(expected: Any, value: Any) -> bool:
    if expected is None:
        return True
    if expected == "object":
        return _is_plain_object(value)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _format_matches(format_name: str, value: str) -> bool:
    if format_name == "uri":
        return urlparse(value).scheme != ""
    if format_name == "date-time":
        if "T" not in value:
            return False
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True
    return True


def _pattern_matches(pattern: str, value: str) -> bool:
    """Match like ECMAScript ``RegExp.test`` for the v1 anchored patterns.

    Python's ``re.search`` lets ``$`` match before a trailing newline, while
    JavaScript's non-multiline ``$`` only matches at end of input. Full-match
    anchored patterns keeps both mirrors aligned.
    """

    if pattern.startswith("^") and pattern.endswith("$"):
        return re.fullmatch(pattern, value) is not None
    return re.search(pattern, value) is not None


def validate(schema: dict[str, Any], data: Any, path: str = "$") -> list[str]:
    errors: list[str] = []

    if not _type_matches(schema.get("type"), data):
        return [f"{path}: 期望 {schema.get('type')}"]

    if "const" in schema and data != schema["const"]:
        errors.append(f"{path}: 必须等于 {json.dumps(schema['const'])}")
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: 必须是 {' / '.join(str(item) for item in schema['enum'])} 之一")
    if "oneOf" in schema:
        branches = schema["oneOf"]
        if not isinstance(branches, list) or not branches:
            errors.append(f"{path}: oneOf 必须是非空数组")
        else:
            branch_errors: list[str] = []
            matched = 0
            for index, branch in enumerate(branches):
                result = validate(branch, data, path)
                if not result:
                    matched += 1
                else:
                    branch_errors.append(f"oneOf[{index}]: {'; '.join(result)}")
            if matched != 1:
                detail = f"：{' | '.join(branch_errors)}" if branch_errors else ""
                errors.append(f"{path}: oneOf 必须恰好匹配一个分支（匹配 {matched} 个）{detail}")

    if isinstance(data, str):
        if "minLength" in schema and len(data) < schema["minLength"]:
            errors.append(f"{path}: 长度不能小于 {schema['minLength']}")
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            errors.append(f"{path}: 长度不能大于 {schema['maxLength']}")
        if "pattern" in schema and not _pattern_matches(schema["pattern"], data):
            errors.append(f"{path}: 不匹配 {schema['pattern']}")
        if "format" in schema and not _format_matches(schema["format"], data):
            errors.append(f"{path}: 不是合法的 {schema['format']}")

    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            errors.append(f"{path}: 不能小于 {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            errors.append(f"{path}: 不能大于 {schema['maximum']}")

    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            errors.append(f"{path}: 至少 {schema['minItems']} 项")
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            errors.append(f"{path}: 最多 {schema['maxItems']} 项")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(data):
                errors.extend(validate(items, item, f"{path}[{index}]"))

    if _is_plain_object(data):
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}.{key}: 必填字段缺失")
        properties = schema.get("properties", {})
        for key, value in data.items():
            if key in properties:
                errors.extend(validate(properties[key], value, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: 不允许的额外字段")

    return errors


def validate_payload(kind: str, payload: Any) -> None:
    """Validate one contract payload; raise ContractInvalid on violations."""

    errors = validate(load_schema(kind), payload)
    if errors:
        raise ContractInvalid(kind, errors)

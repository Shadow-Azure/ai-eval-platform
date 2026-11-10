"""M6-01 (#90): schema and backend trace-manifest validation agree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.runner_contracts import ContractInvalid, validate_payload
from app.services.trace_manifest import (
    TraceManifestError,
    normalize_trace_manifest,
    normalize_upload_objects,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runner"

COMPLETION_VALID = (
    "completion.multitrace.valid.json",
    "completion.trace-legacy.valid.json",
)
COMPLETION_INVALID = (
    "completion.trace-empty-manifest.invalid.json",
    "completion.trace-legacy-missing-digest.invalid.json",
    "completion.trace-mixed-shape.invalid.json",
    "completion.trace-dotdot-path.invalid.json",
    "completion.trace-newline-path.invalid.json",
)
UPLOAD_VALID = ("upload.multifile.valid.json",)
UPLOAD_INVALID = ("upload.invalid-traversal-path.json", "upload.invalid-logical-path.json")


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", COMPLETION_VALID)
def test_valid_completion_fixture_passes_schema_and_normalizer(name: str) -> None:
    payload = _payload(name)
    validate_payload("completion", payload)
    assert normalize_trace_manifest(payload["trace_manifest"])


@pytest.mark.parametrize("name", COMPLETION_INVALID)
def test_invalid_completion_fixture_is_rejected_everywhere(name: str) -> None:
    payload = _payload(name)
    with pytest.raises(ContractInvalid):
        validate_payload("completion", payload)
    with pytest.raises(TraceManifestError):
        normalize_trace_manifest(payload["trace_manifest"])


@pytest.mark.parametrize("name", UPLOAD_VALID)
def test_valid_upload_fixture_passes_schema_and_normalizer(name: str) -> None:
    payload = _payload(name)
    validate_payload("upload", payload)
    assert normalize_upload_objects(payload["objects"])


@pytest.mark.parametrize("name", UPLOAD_INVALID)
def test_invalid_upload_fixture_is_rejected_everywhere(name: str) -> None:
    payload = _payload(name)
    with pytest.raises(ContractInvalid):
        validate_payload("upload", payload)
    with pytest.raises(TraceManifestError):
        normalize_upload_objects(payload["objects"])

"""Versioned trace parser protocol and normalized value objects (M6-02)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True)
class ParseInput:
    runtime_key: str
    runtime_version: str | None
    native_format: str | None
    format_version: str | None
    detected_format: str
    logical_path: str | None
    object_key: str
    sha256: str
    content_type: str | None
    size: int
    artifact_kind: str | None = None


@dataclass
class ParsedSpan:
    ordinal: int
    kind: str
    native_event_id: str | None = None
    parent_native_id: str | None = None
    name: str | None = None
    status: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: Decimal | None = None
    content_preview: str | None = None
    content_object_key: str | None = None


@dataclass
class ParsedSession:
    native_session_id: str
    ordinal: int = 0
    root_session_id: str | None = None
    parent_native_session_id: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    status: str = "ready"
    relation_evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    spans: list[ParsedSpan] = field(default_factory=list)


@dataclass
class ParsedFile:
    sessions: list[ParsedSession] = field(default_factory=list)
    status: str = "ready"
    detected_format: str = "unknown"
    error_code: str | None = None
    error_summary: str | None = None
    line_no: int | None = None


class ParseError(Exception):
    """Fatal per-file parse error; ingestion isolates it from other files."""

    def __init__(self, code: str, summary: str, *, line_no: int | None = None) -> None:
        self.code = code
        self.summary = summary
        self.line_no = line_no
        super().__init__(summary)


class TraceParser(Protocol):
    parser_version: str
    runtime_keys: tuple[str, ...]
    formats: tuple[str, ...]
    format_versions: tuple[str, ...]

    def parse(self, data: bytes, context: ParseInput) -> ParsedFile: ...

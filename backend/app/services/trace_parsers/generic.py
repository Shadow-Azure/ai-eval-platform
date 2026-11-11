"""Deterministic raw JSONL/JSON fallback parser (M6-02)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any

from ..trace_redaction import redact_text
from .base import ParsedFile, ParsedSession, ParsedSpan, ParseInput

MAX_LINES = 5000
MAX_PREVIEW_SOURCE = 2000
SUPPORTED_FORMAT_VERSIONS = frozenset({"1"})

_SESSION_KEYS = ("sessionId", "session_id", "conversation_id", "thread_id")
_TOKEN_KEYS = {
    "input": ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
    "output": ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"),
}


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _session_id(record: dict[str, Any], nested: dict[str, Any] | None = None) -> str | None:
    for key in _SESSION_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value[:200]
    session = record.get("session")
    if isinstance(session, dict) and isinstance(session.get("id"), str) and session["id"]:
        return session["id"][:200]
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("session_id"), str):
        return message["session_id"][:200]
    if nested is not None and isinstance(nested.get("id"), str) and nested["id"]:
        return nested["id"][:200]
    return None


def _event_id(record: dict[str, Any], line_no: int) -> str:
    for key in ("uuid", "event_id", "id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value[:200]
    return f"line-{line_no}"


def _usage(record: dict[str, Any]) -> tuple[int | None, int | None, Decimal | None]:
    usage = record.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = next(
        (_int(usage[key]) for key in _TOKEN_KEYS["input"] if _int(usage.get(key)) is not None),
        None,
    )
    output_tokens = next(
        (_int(usage[key]) for key in _TOKEN_KEYS["output"] if _int(usage.get(key)) is not None),
        None,
    )
    for key in ("cost", "total_cost", "totalCost"):
        cost = _decimal(usage.get(key))
        if cost is not None:
            return input_tokens, output_tokens, cost
    return input_tokens, output_tokens, _decimal(record.get("cost"))


def _preview(record: dict[str, Any]) -> str:
    try:
        raw = json.dumps(record, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = str(record)
    return redact_text(raw[:MAX_PREVIEW_SOURCE], limit=500)


def _span(record: dict[str, Any], *, line_no: int, ordinal: int) -> ParsedSpan:
    input_tokens, output_tokens, cost = _usage(record)
    event_type = record.get("type")
    name = record.get("name") or event_type or record.get("role") or "message"
    return ParsedSpan(
        ordinal=ordinal,
        kind="MESSAGE",
        native_event_id=_event_id(record, line_no),
        name=redact_text(name, limit=300),
        status=(
            record.get("status")
            if isinstance(record.get("status"), str)
            else None
        ),
        started_at=_timestamp(record.get("timestamp") or record.get("created_at")),
        attributes={
            "line_no": line_no,
            "type": redact_text(event_type, limit=120) if event_type is not None else None,
        },
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        content_preview=_preview(record),
    )


def _agent_identity(logical_path: str | None) -> str | None:
    """Stable agent identity from the full logical path, extension stripped.

    Generic exports may reuse the root ``sessionId`` for subagent files. The
    directory-preserving path disambiguates two same-named files without
    guessing runtime-specific relation semantics.
    """

    if not logical_path:
        return None
    path = PurePosixPath(logical_path)
    stem = (
        path.with_suffix("").as_posix().lstrip("/")
        if path.suffix
        else path.as_posix().lstrip("/")
    )
    return stem[:200] or None


class GenericJsonlParser:
    """Index native records without guessing runtime-specific relationships."""

    parser_version = "raw-jsonl-1"
    runtime_keys = ("*",)
    formats = ("jsonl", "json", "text", "unknown")
    format_versions = ("*",)

    def parse(self, data: bytes, context: ParseInput) -> ParsedFile:
        if (
            context.format_version is not None
            and context.format_version not in SUPPORTED_FORMAT_VERSIONS
        ):
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="unknown_format_version",
                error_summary=(
                    "未知原生格式版本："
                    f"{redact_text(context.format_version, limit=80)}"
                ),
                line_no=1,
            )

        # Subagent ``*.meta.json`` files are relation evidence, not an
        # execution context. The trace_file row is kept for provenance, but the
        # generic parser must not invent a synthetic session from metadata.
        if context.artifact_kind == "trace_metadata":
            return ParsedFile(
                sessions=[],
                status="ready",
                detected_format=context.detected_format,
            )

        text = data.decode("utf-8", errors="replace")
        records: list[tuple[int, dict[str, Any]]] = []
        corrupt_lines: list[int] = []
        limit_exceeded = False

        if context.detected_format == "json":
            try:
                document = json.loads(text)
            except json.JSONDecodeError:
                corrupt_lines.append(1)
            else:
                items = document if isinstance(document, list) else [document]
                for index, item in enumerate(items[:MAX_LINES], start=1):
                    if isinstance(item, dict):
                        records.append((index, item))
                    else:
                        corrupt_lines.append(index)
                limit_exceeded = len(items) > MAX_LINES
        else:
            for line_no, line in enumerate(text.splitlines(), start=1):
                if line_no > MAX_LINES:
                    limit_exceeded = True
                    break
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    corrupt_lines.append(line_no)
                    continue
                if isinstance(item, dict):
                    records.append((line_no, item))
                else:
                    corrupt_lines.append(line_no)

        if not records:
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="empty_trace",
                error_summary="轨迹文件没有可解析记录",
                line_no=corrupt_lines[0] if corrupt_lines else None,
            )

        by_session: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        order: list[str] = []
        fallback_id = f"file-{context.sha256[:16]}"
        agent_identity = _agent_identity(context.logical_path)
        for line_no, record in records:
            session_id = _session_id(record) or fallback_id
            if session_id not in by_session:
                by_session[session_id] = []
                order.append(session_id)
            by_session[session_id].append((line_no, record))

        sessions: list[ParsedSession] = []
        for ordinal, session_id in enumerate(order):
            items = by_session[session_id]
            spans = [
                _span(record, line_no=line_no, ordinal=index)
                for index, (line_no, record) in enumerate(items)
            ]
            timestamps = [span.started_at for span in spans if span.started_at is not None]
            sessions.append(
                ParsedSession(
                    native_session_id=session_id,
                    root_session_id=session_id,
                    agent_id=agent_identity,
                    ordinal=ordinal,
                    status="ready",
                    metadata={"record_count": len(items), "detected_format": context.detected_format},
                    started_at=min(timestamps) if timestamps else None,
                    ended_at=max(timestamps) if timestamps else None,
                    spans=spans,
                )
            )

        status = "ready"
        error_code = None
        error_summary = None
        line_no = None
        if context.detected_format in ("unknown", "text"):
            status = "degraded"
            error_code = "unknown_format"
            error_summary = "未识别原生格式，按通用 JSONL 降级解析"
            line_no = 1
        if corrupt_lines:
            status = "degraded"
            error_code = "invalid_jsonl"
            error_summary = (
                f"{len(corrupt_lines)} 行不是有效 JSON（首个第 {corrupt_lines[0]} 行）"
            )
            line_no = corrupt_lines[0]
        elif limit_exceeded:
            status = "degraded"
            error_code = "line_limit_exceeded"
            error_summary = f"超过 {MAX_LINES} 行上限，仅导入前 {MAX_LINES} 行"
            line_no = MAX_LINES + 1
        if status == "degraded":
            for session in sessions:
                session.status = "degraded"

        return ParsedFile(
            sessions=sessions,
            status=status,
            detected_format=context.detected_format,
            error_code=error_code,
            error_summary=error_summary,
            line_no=line_no,
        )

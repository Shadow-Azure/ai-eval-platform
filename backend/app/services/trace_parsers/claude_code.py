"""Claude Code 2.1.278 native JSONL normalizer (M6-03, design 08 §4.1)."""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any

from ..trace_redaction import redact_text
from .base import (
    ParsedFile,
    ParsedRelation,
    ParsedSession,
    ParsedSpan,
    ParseInput,
)

PARSER_VERSION = "claude-code-2.1.278-v1"
SUPPORTED_FORMAT_VERSIONS = frozenset({"1"})
MAX_PREVIEW = 500
MAX_ERROR_SUMMARY = 240

_IGNORED_RECORD_TYPES = {
    "file-history-delta",
    "file-history-snapshot",
    "atis-latch",
    "ai-title",
    "last-prompt",
    "mode",
    "pr-link",
    "queue-operation",
}


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


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


def _preview(value: Any) -> tuple[str, bool]:
    if value is None:
        return "", False
    if isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            raw = str(value)
    return redact_text(raw, limit=MAX_PREVIEW), len(raw) > MAX_PREVIEW


def _first_string(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _agent_id_from_path(logical_path: str | None) -> str | None:
    if not logical_path:
        return None
    parts = PurePosixPath(logical_path).parts
    if "subagents" not in parts:
        return None
    name = parts[-1]
    if not name.startswith("agent-"):
        return None
    if name.endswith(".meta.json"):
        name = name[: -len(".meta.json")]
    elif name.endswith(".jsonl"):
        name = name[: -len(".jsonl")]
    else:
        return None
    return name[len("agent-") :] or None


def _root_session_from_path(logical_path: str | None) -> str | None:
    if not logical_path:
        return None
    parts = PurePosixPath(logical_path).parts
    if "subagents" not in parts:
        return None
    index = parts.index("subagents")
    return parts[index - 1] if index > 0 else None


def _record_parent(record: dict[str, Any], by_uuid: dict[str, dict[str, Any]]) -> str | None:
    parent_uuid = record.get("parentUuid")
    if not isinstance(parent_uuid, str) or not parent_uuid:
        return None
    parent = by_uuid.get(parent_uuid)
    if parent and parent.get("type") == "assistant":
        message = parent.get("message")
        if isinstance(message, dict) and isinstance(message.get("id"), str):
            return f"message:{message['id']}"
    return f"record:{parent_uuid}"


def _session_key(session: ParsedSession) -> str:
    return f"{session.native_session_id}:{session.agent_id or 'root'}"[:400]


def _base_attributes(
    context: ParseInput,
    *,
    session_id: str,
    agent_id: str | None,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "gen_ai.conversation.id": session_id,
        "eval.runtime.version": context.runtime_version or "unknown",
        "eval.parser.version": PARSER_VERSION,
    }
    if agent_id:
        attributes["gen_ai.agent.id"] = agent_id
    if record:
        attributes["trace.parent_uuid"] = record.get("parentUuid")
        attributes["claude.is_sidechain"] = bool(record.get("isSidechain"))
        version = record.get("version")
        if isinstance(version, str):
            attributes["claude.native_version"] = version
    return attributes


def _tool_name(block: dict[str, Any]) -> str:
    value = block.get("name")
    return redact_text(value, limit=120) if isinstance(value, str) and value else "tool"


def _tool_call_id(block: dict[str, Any]) -> str | None:
    value = block.get("id")
    if isinstance(value, str) and value:
        return redact_text(value, limit=200)
    return None


def _content_block_spans(
    *,
    record: dict[str, Any],
    message: dict[str, Any],
    record_uuid: str,
    llm_id: str,
    next_ordinal: int,
    context: ParseInput,
    session_id: str,
    agent_id: str | None,
) -> list[ParsedSpan]:
    content = message.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []

    spans: list[ParsedSpan] = []
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        native_id = f"{record_uuid}:block:{index}"
        kind = "MESSAGE"
        name = "message"
        preview_source: Any = block.get("text")
        parent_id = llm_id
        attributes = _base_attributes(context, session_id=session_id, agent_id=agent_id, record=record)
        attributes["gen_ai.operation.name"] = "message"
        attributes["openinference.span.kind"] = "CHAIN"

        if block_type == "thinking":
            kind = "THINKING"
            name = "thinking"
            preview_source = block.get("thinking")
            attributes["gen_ai.operation.name"] = "reasoning"
            attributes["openinference.span.kind"] = "LLM"
        elif block_type == "tool_use":
            call_id = _tool_call_id(block)
            if call_id:
                native_id = f"tool:{call_id}"
            tool_name = _tool_name(block)
            kind = "AGENT" if tool_name.lower() == "agent" else "TOOL"
            name = tool_name
            preview_source = block.get("input")
            attributes["gen_ai.operation.name"] = "execute_tool"
            attributes["gen_ai.tool.name"] = tool_name
            attributes["openinference.span.kind"] = kind
            if call_id:
                attributes["gen_ai.tool.call.id"] = call_id
        elif block_type == "tool_result":
            call_id = _first_string(block, "tool_use_id", "toolUseId")
            if isinstance(call_id, str) and call_id:
                native_id = f"tool-result:{redact_text(call_id, limit=200)}"
                parent_id = f"tool:{call_id}"
                attributes["gen_ai.tool.call.id"] = redact_text(call_id, limit=200)
            kind = "TOOL"
            name = "tool_result"
            preview_source = block.get("content")
            attributes["gen_ai.operation.name"] = "execute_tool"
            attributes["openinference.span.kind"] = "TOOL"
            if block.get("is_error") is True:
                attributes["error.type"] = "tool_error"
        elif block_type == "text":
            preview_source = block.get("text")
        else:
            preview_source = block

        preview, truncated = _preview(preview_source)
        content_object_key = None
        if preview:
            attributes["content.media_type"] = "text/plain"
        if truncated:
            attributes["content.truncated"] = True
            attributes["content.externalized"] = True
            attributes["content.locator"] = f"{record_uuid}:block:{index}"
            attributes["content.original_chars"] = len(
                preview_source if isinstance(preview_source, str) else json.dumps(preview_source, default=str)
            )
            content_object_key = context.object_key

        spans.append(
            ParsedSpan(
                ordinal=next_ordinal + len(spans),
                kind=kind,
                native_event_id=native_id,
                parent_native_id=parent_id,
                name=name,
                started_at=_timestamp(record.get("timestamp")),
                ended_at=_timestamp(record.get("timestamp")),
                attributes=attributes,
                content_preview=preview or None,
                content_object_key=content_object_key,
            )
        )

        if kind == "AGENT" and isinstance(attributes.get("gen_ai.tool.call.id"), str):
            spans[-1].attributes["claude.parent_session_id"] = session_id
            spans[-1].attributes["claude.spawn.tool_use_id"] = attributes["gen_ai.tool.call.id"]

    return spans


def _annotate_result_agent_ids(
    records: list[dict[str, Any]],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for record in records:
        tool_result = record.get("toolUseResult")
        if not isinstance(tool_result, dict):
            continue
        agent_id = _first_string(tool_result, "agentId", "agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call_id = _first_string(block, "tool_use_id", "toolUseId")
            if isinstance(call_id, str) and call_id:
                result[call_id] = redact_text(agent_id, limit=200)
    return result


class ClaudeCodeParser:
    """Parse final Claude Code files; never infer ancestry from time or sessionId."""

    parser_version = PARSER_VERSION
    runtime_keys = ("claude-code", "claude_code")
    formats = ("jsonl", "json", "unknown")
    # Unknown future versions are routed here so the parser can retain the raw
    # file and return an explicit degraded result instead of failing enqueue.
    format_versions = ("*",)

    def parse(self, data: bytes, context: ParseInput) -> ParsedFile:
        if context.detected_format == "unknown":
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="unknown_format",
                error_summary="未识别 Claude Code 原生格式，保留原件并等待显式版本适配",
                line_no=1,
            )
        if context.format_version is not None and context.format_version not in SUPPORTED_FORMAT_VERSIONS:
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="unknown_format_version",
                error_summary=(f"未知 Claude Code 原生格式版本：{redact_text(context.format_version, limit=80)}"),
                line_no=1,
            )
        if context.artifact_kind == "trace_metadata":
            return self._parse_metadata(data, context)
        return self._parse_session(data, context)

    def _parse_metadata(self, data: bytes, context: ParseInput) -> ParsedFile:
        try:
            payload = json.loads(data.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="invalid_json",
                error_summary="Claude Code 子 Agent 元数据不是有效 JSON",
                line_no=1,
            )
        if not isinstance(payload, dict):
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="invalid_metadata",
                error_summary="Claude Code 子 Agent 元数据必须是对象",
                line_no=1,
            )
        agent_id = _agent_id_from_path(context.logical_path)
        tool_use_id = _first_string(payload, "toolUseId", "tool_use_id")
        if agent_id is None or not isinstance(tool_use_id, str) or not tool_use_id:
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="invalid_subagent_metadata",
                error_summary="子 Agent 元数据缺少 agent 文件名或 toolUseId",
                line_no=1,
            )
        evidence = {
            "agent_type": redact_text(_first_string(payload, "agentType", "agent_type"), limit=200)
            if _first_string(payload, "agentType", "agent_type") is not None
            else None,
            "description": redact_text(payload.get("description"), limit=300)
            if payload.get("description") is not None
            else None,
            "model": redact_text(payload.get("model"), limit=200) if payload.get("model") is not None else None,
            "spawn_depth": _int(payload.get("spawnDepth", payload.get("spawn_depth"))),
            "tool_use_id": redact_text(tool_use_id, limit=200),
        }
        return ParsedFile(
            relations=[
                ParsedRelation(
                    kind="metadata",
                    child_agent_id=agent_id,
                    tool_use_id=evidence["tool_use_id"],
                    parent_native_session_id=_root_session_from_path(context.logical_path),
                    evidence=evidence,
                )
            ],
            status="ready",
            detected_format=context.detected_format,
        )

    def _parse_session(self, data: bytes, context: ParseInput) -> ParsedFile:
        text = data.decode("utf-8", errors="replace")
        records: list[dict[str, Any]] = []
        corrupt_lines: list[int] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                corrupt_lines.append(line_no)
                continue
            if isinstance(item, dict):
                records.append(item)
            else:
                corrupt_lines.append(line_no)
        if not records:
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="empty_trace",
                error_summary="Claude Code 会话文件没有可解析记录",
                line_no=corrupt_lines[0] if corrupt_lines else None,
            )

        session_id = next(
            (value for record in records if (value := _first_string(record, "sessionId", "session_id"))),
            None,
        )
        if session_id is None:
            session_id = _root_session_from_path(context.logical_path)
        if session_id is None:
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="missing_session_id",
                error_summary="Claude Code 会话文件缺少 sessionId",
                line_no=1,
            )

        path_agent_id = _agent_id_from_path(context.logical_path)
        record_agent_id = next(
            (value for record in records if (value := _first_string(record, "agentId", "agent_id"))),
            None,
        )
        if path_agent_id and record_agent_id and path_agent_id != record_agent_id:
            return ParsedFile(
                status="degraded",
                detected_format=context.detected_format,
                error_code="agent_id_mismatch",
                error_summary="子 Agent 文件名与原生记录 agentId 不一致",
                line_no=1,
            )
        agent_id = path_agent_id or record_agent_id
        is_sidechain = any(record.get("isSidechain") is True for record in records)
        stem = PurePosixPath(context.logical_path or "").stem
        native_agent_kind = "subagent" if agent_id is not None and (is_sidechain or path_agent_id) else "root"
        if native_agent_kind == "root" and stem and stem != session_id:
            native_agent_kind = "resume"
            agent_id = f"resume:{redact_text(stem, limit=180)}"

        deduplicated: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for index, record in enumerate(records):
            uuid = record.get("uuid")
            key = uuid if isinstance(uuid, str) and uuid else f"line:{index}"
            deduplicated.setdefault(key, record)
        unique_records = list(deduplicated.values())
        by_uuid = {record["uuid"]: record for record in unique_records if isinstance(record.get("uuid"), str)}
        result_agent_ids = _annotate_result_agent_ids(unique_records)

        session = ParsedSession(
            native_session_id=session_id,
            root_session_id=session_id,
            agent_id=agent_id,
            status="unlinked" if native_agent_kind == "subagent" else "ready",
            metadata={
                "native_agent_kind": native_agent_kind,
                "runtime_key": "claude-code",
            },
        )
        if native_agent_kind == "subagent":
            session.relation_evidence = {
                "type": "claude.sidechain",
                "confidence": "derived",
                "rule_version": PARSER_VERSION,
                "agent_id": agent_id,
                "is_sidechain": True,
            }

        agent_span = ParsedSpan(
            ordinal=0,
            kind="AGENT",
            native_event_id=f"agent:{agent_id or session_id}",
            name=agent_id or "Claude Code root",
            attributes={
                **_base_attributes(context, session_id=session_id, agent_id=agent_id),
                "gen_ai.operation.name": "invoke_agent",
                "openinference.span.kind": "AGENT",
            },
            started_at=_timestamp(unique_records[0].get("timestamp")),
        )
        session.spans.append(agent_span)

        message_usage: OrderedDict[str, tuple[int | None, int | None, str | None]] = OrderedDict()
        message_parents: dict[str, str | None] = {}
        llm_spans: dict[str, ParsedSpan] = {}
        relations: list[ParsedRelation] = []
        timestamps = [value for record in unique_records if (value := _timestamp(record.get("timestamp"))) is not None]
        final_cost: Decimal | None = None

        for record in unique_records:
            record_type = record.get("type")
            if record_type in _IGNORED_RECORD_TYPES:
                continue
            if record_type == "cost-state":
                final_cost = _decimal(record.get("totalCostUSD"))
                started = _timestamp(record.get("startTime"))
                if started:
                    timestamps.append(started)
                continue
            uuid = record.get("uuid")
            record_uuid = redact_text(uuid, limit=200) if isinstance(uuid, str) and uuid else "record"
            parent_id = _record_parent(record, by_uuid)
            record_timestamp = _timestamp(record.get("timestamp"))
            message = record.get("message")
            if not isinstance(message, dict):
                message = {}

            if record_type == "assistant":
                native_message_id = message.get("id")
                msg_id = (
                    redact_text(native_message_id, limit=200)
                    if isinstance(native_message_id, str) and native_message_id
                    else record_uuid
                )
                llm_id = f"message:{msg_id}"
                message_parents.setdefault(msg_id, parent_id)
                usage = message.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                tokens = (
                    _int(usage.get("input_tokens")),
                    _int(usage.get("output_tokens")),
                    None,
                )
                if msg_id not in message_usage or (
                    message_usage[msg_id][0] is None
                    and message_usage[msg_id][1] is None
                    and (tokens[0] is not None or tokens[1] is not None)
                ):
                    message_usage[msg_id] = tokens
                llm = llm_spans.get(llm_id)
                if llm is None:
                    attributes = {
                        **_base_attributes(
                            context,
                            session_id=session_id,
                            agent_id=agent_id,
                            record=record,
                        ),
                        "gen_ai.operation.name": "chat",
                        "gen_ai.request.model": redact_text(message.get("model"), limit=200)
                        if message.get("model") is not None
                        else None,
                        "openinference.span.kind": "LLM",
                    }
                    for usage_key in (
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    ):
                        token_value = _int(usage.get(usage_key))
                        if token_value is not None:
                            attributes[f"gen_ai.usage.{usage_key}"] = token_value
                    output_details = usage.get("output_tokens_details")
                    if isinstance(output_details, dict):
                        thinking_tokens = _int(output_details.get("thinking_tokens"))
                        if thinking_tokens is not None:
                            attributes["gen_ai.usage.thinking_tokens"] = thinking_tokens
                    llm = ParsedSpan(
                        ordinal=len(session.spans),
                        kind="LLM",
                        native_event_id=llm_id,
                        parent_native_id=message_parents[msg_id],
                        name=attributes.get("gen_ai.request.model") or "assistant",
                        status=redact_text(message.get("stop_reason"), limit=32)
                        if message.get("stop_reason") is not None
                        else None,
                        started_at=record_timestamp,
                        ended_at=record_timestamp,
                        attributes=attributes,
                        input_tokens=tokens[0],
                        output_tokens=tokens[1],
                    )
                    llm_spans[llm_id] = llm
                    session.spans.append(llm)
                else:
                    if tokens[0] is not None:
                        llm.input_tokens = tokens[0]
                    if tokens[1] is not None:
                        llm.output_tokens = tokens[1]
                blocks = _content_block_spans(
                    record=record,
                    message=message,
                    record_uuid=record_uuid,
                    llm_id=llm_id,
                    next_ordinal=len(session.spans),
                    context=context,
                    session_id=session_id,
                    agent_id=agent_id,
                )
                session.spans.extend(blocks)
                for block_span in blocks:
                    if block_span.kind == "AGENT" and (call_id := block_span.attributes.get("gen_ai.tool.call.id")):
                        child_agent_id = result_agent_ids.get(call_id)
                        block_span.attributes["claude.child.agent_id"] = child_agent_id
                        if child_agent_id:
                            relations.append(
                                ParsedRelation(
                                    kind="spawn",
                                    child_agent_id=child_agent_id,
                                    tool_use_id=call_id,
                                    parent_native_session_id=session_id,
                                    parent_session_key=_session_key(session),
                                    evidence={
                                        "type": "claude.agent_tool_result",
                                        "tool_use_id": call_id,
                                        "tool_result_agent_id": child_agent_id,
                                    },
                                )
                            )
            elif record_type in ("user", "system"):
                primary_id = f"record:{record_uuid}"
                preview, truncated = _preview(message.get("content", record.get("content")))
                message_object_key = None
                attributes = {
                    **_base_attributes(
                        context,
                        session_id=session_id,
                        agent_id=agent_id,
                        record=record,
                    ),
                    "gen_ai.operation.name": "message",
                    "openinference.span.kind": "CHAIN",
                }
                if preview:
                    attributes["content.media_type"] = "text/plain"
                if truncated:
                    attributes["content.truncated"] = True
                    attributes["content.externalized"] = True
                    attributes["content.locator"] = f"{record_uuid}:message"
                    message_object_key = context.object_key
                session.spans.append(
                    ParsedSpan(
                        ordinal=len(session.spans),
                        kind="MESSAGE",
                        native_event_id=primary_id,
                        parent_native_id=parent_id,
                        name=record_type,
                        started_at=record_timestamp,
                        ended_at=record_timestamp,
                        attributes=attributes,
                        content_preview=preview or None,
                        content_object_key=message_object_key,
                    )
                )
                if record_type == "user" and isinstance(message.get("content"), list):
                    blocks = _content_block_spans(
                        record=record,
                        message=message,
                        record_uuid=record_uuid,
                        llm_id=primary_id,
                        next_ordinal=len(session.spans),
                        context=context,
                        session_id=session_id,
                        agent_id=agent_id,
                    )
                    # Tool results belong to the matching tool call, not the
                    # enclosing user record; preserve the record as their
                    # fallback parent when no call exists.
                    session.spans.extend(blocks)

        unique_spans: OrderedDict[str, ParsedSpan] = OrderedDict()
        for span in session.spans:
            key = span.native_event_id or f"anonymous:{len(unique_spans)}"
            unique_spans.setdefault(key, span)
        session.spans = list(unique_spans.values())

        known_ids = {span.native_event_id for span in session.spans if span.native_event_id}
        for span in session.spans:
            if span.parent_native_id and span.parent_native_id not in known_ids:
                span.attributes["trace.fragment_root"] = True
                span.parent_native_id = None

        total_input = sum(value[0] or 0 for value in message_usage.values())
        total_output = sum(value[1] or 0 for value in message_usage.values())
        agent_span.input_tokens = total_input
        agent_span.output_tokens = total_output
        agent_span.cost = final_cost
        agent_span.ended_at = max(timestamps, default=None)
        session.started_at = min(timestamps, default=None)
        session.ended_at = max(timestamps, default=None)
        session.metadata["final_usage"] = {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cost_usd": str(final_cost) if final_cost is not None else None,
        }
        session.metadata["message_count"] = len(message_usage)
        session.metadata["record_count"] = len(unique_records)
        if relations:
            session.metadata["agent_links"] = [
                {
                    "child_agent_id": relation.child_agent_id,
                    "tool_use_id": relation.tool_use_id,
                }
                for relation in relations
            ]
        session.spans.sort(key=lambda span: span.ordinal)
        for ordinal, span in enumerate(session.spans):
            span.ordinal = ordinal

        status = "ready"
        error_code = None
        error_summary = None
        line_no = None
        if corrupt_lines:
            status = "degraded"
            error_code = "invalid_jsonl"
            error_summary = f"{len(corrupt_lines)} 行不是有效 JSON（首个第 {corrupt_lines[0]} 行）"
            line_no = corrupt_lines[0]
            session.status = "degraded"

        return ParsedFile(
            sessions=[session],
            relations=relations,
            status=status,
            detected_format=context.detected_format,
            error_code=error_code,
            error_summary=error_summary,
            line_no=line_no,
        )


def resolve_cross_file_relations(parsed_files: list[ParsedFile]) -> None:
    """Resolve Claude ancestry only when Agent call and metadata evidence agree."""

    spawn_links: dict[str, list[ParsedRelation]] = {}
    metadata_links: dict[str, list[ParsedRelation]] = {}
    for parsed in parsed_files:
        for relation in parsed.relations:
            if relation.kind == "spawn":
                spawn_links.setdefault(relation.child_agent_id, []).append(relation)
            elif relation.kind == "metadata":
                metadata_links.setdefault(relation.child_agent_id, []).append(relation)

    child_sessions: set[str] = set()
    for parsed in parsed_files:
        for session in parsed.sessions:
            if session.metadata.get("native_agent_kind") != "subagent":
                continue
            child_agent_id = session.agent_id
            if not child_agent_id:
                continue
            child_sessions.add(child_agent_id)
            spawns = {
                (relation.tool_use_id, relation.parent_native_session_id): relation
                for relation in spawn_links.get(child_agent_id) or []
            }
            metadata_records = {
                (relation.tool_use_id, relation.parent_native_session_id): relation
                for relation in metadata_links.get(child_agent_id) or []
            }
            evidence = dict(session.relation_evidence or {})
            if len(spawns) > 1 or len(metadata_records) > 1:
                session.status = "unlinked"
                session.relation_evidence = {
                    **evidence,
                    "reason": "ambiguous_parent_evidence",
                }
                _degrade(
                    parsed,
                    "ambiguous_parent_evidence",
                    "子 Agent 存在多个不一致的父子关系证据",
                )
                continue
            spawn = next(iter(spawns.values()), None)
            metadata = next(iter(metadata_records.values()), None)
            if spawn is None:
                session.status = "unlinked"
                session.relation_evidence = {
                    **evidence,
                    "reason": "missing_root_spawn_evidence",
                }
                _degrade(parsed, "missing_root_spawn_evidence", "缺少主文件 Agent 调用证据")
                continue
            if metadata is None:
                session.status = "unlinked"
                session.relation_evidence = {
                    **evidence,
                    "reason": "missing_metadata",
                    "tool_use_id": spawn.tool_use_id,
                }
                _degrade(parsed, "missing_subagent_metadata", "子 Agent 元数据缺失")
                continue
            if not spawn.tool_use_id or spawn.tool_use_id != metadata.tool_use_id:
                session.status = "unlinked"
                session.relation_evidence = {
                    **evidence,
                    "reason": "metadata_tool_use_mismatch",
                    "tool_use_id": spawn.tool_use_id,
                }
                _degrade(
                    parsed,
                    "metadata_tool_use_mismatch",
                    "子 Agent 元数据 toolUseId 与主文件 Agent 调用不一致",
                )
                continue
            parent_session_key = spawn.parent_session_key
            if not parent_session_key and spawn.parent_native_session_id:
                parent_session_key = f"{spawn.parent_native_session_id}:root"
            if not parent_session_key:
                session.status = "unlinked"
                session.relation_evidence = {
                    **evidence,
                    "reason": "missing_parent_session",
                }
                _degrade(parsed, "missing_parent_session", "无法确定父 session")
                continue
            if (
                spawn.parent_native_session_id
                and metadata.parent_native_session_id
                and spawn.parent_native_session_id != metadata.parent_native_session_id
            ):
                session.status = "unlinked"
                session.relation_evidence = {
                    **evidence,
                    "reason": "parent_session_mismatch",
                    "parent_native_session_id": spawn.parent_native_session_id,
                    "metadata_parent_native_session_id": metadata.parent_native_session_id,
                }
                _degrade(
                    parsed,
                    "parent_session_mismatch",
                    "子 Agent 元数据目录与主文件 session 不一致",
                )
                continue
            session.parent_native_session_id = parent_session_key
            session.status = "ready"
            session.agent_name = metadata.evidence.get("agent_type")
            session.metadata.update(
                {
                    "spawn_depth": metadata.evidence.get("spawn_depth"),
                    "metadata_model": metadata.evidence.get("model"),
                    "description": metadata.evidence.get("description"),
                }
            )
            session.relation_evidence = {
                "type": "claude.agent_tool_result+metadata",
                "confidence": "authoritative",
                "tool_use_id": spawn.tool_use_id,
                "parent_native_session_id": metadata.parent_native_session_id,
                "parent_session_key": parent_session_key,
                "child_agent_id": child_agent_id,
                "tool_result_evidence": spawn.evidence,
                "metadata_evidence": metadata.evidence,
            }
            for span in session.spans:
                if span.native_event_id == f"agent:{child_agent_id}":
                    span.name = session.agent_name or child_agent_id
                    span.attributes["gen_ai.agent.name"] = session.agent_name
                    break

    for parsed in parsed_files:
        for relation in parsed.relations:
            if relation.kind != "spawn" or relation.child_agent_id in child_sessions:
                continue
            parent_key = relation.parent_session_key
            for session in parsed.sessions:
                if parent_key and _session_key(session) == parent_key:
                    session.status = "degraded"
                    session.relation_evidence = {
                        **dict(session.relation_evidence or {}),
                        "missing_child_agent_ids": sorted(
                            set(
                                item.child_agent_id
                                for item in parsed.relations
                                if item.kind == "spawn" and item.child_agent_id not in child_sessions
                            )
                        ),
                    }
                    _degrade(
                        parsed,
                        "missing_child_trace",
                        "主文件声明了子 Agent，但对应子会话文件缺失",
                    )
                    break


def _degrade(parsed: ParsedFile, code: str, summary: str) -> None:
    parsed.status = "degraded"
    if parsed.error_code is None:
        parsed.error_code = code
        parsed.error_summary = redact_text(summary, limit=MAX_ERROR_SUMMARY)

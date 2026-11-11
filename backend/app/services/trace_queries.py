"""Attempt-scoped trace queries for the M6 diagnosis console."""

from __future__ import annotations

import base64
import binascii
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    TraceFile,
    TraceSession,
    TraceSpan,
)
from ..models.trace import TRACE_SESSION_STATUSES
from .evaluations import iso_utc
from .trace_redaction import redact_text


class TraceQueryError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


_SENSITIVE_ATTRIBUTE_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "token",
    "password",
    "cookie",
    "private_key",
)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE_ATTRIBUTE_PARTS):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _sanitize(item)
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return redact_text(value, limit=2000)
    return value


def _encode_cursor(session_id: str, ordinal: int, span_id: str) -> str:
    raw = json.dumps(
        {"session_id": session_id, "ordinal": ordinal, "span_id": span_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(session_id: str, cursor: str | None) -> tuple[int, str] | None:
    if cursor is None:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode((cursor + padding).encode()).decode()
        )
        ordinal = payload.get("ordinal")
        span_id = payload.get("span_id")
        if (
            not isinstance(payload.get("session_id"), str)
            or payload["session_id"] != session_id
            or not isinstance(ordinal, int)
            or ordinal < 0
            or not isinstance(span_id, str)
            or not span_id
            or len(span_id) > 64
        ):
            raise ValueError("cursor shape")
        return ordinal, span_id
    except (
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        binascii.Error,
        UnicodeDecodeError,
    ) as exc:
        raise TraceQueryError(422, "invalid_cursor", "轨迹分页游标无效") from exc


def _session_or_404(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    session_id: str,
) -> TraceSession:
    trace_session = session.scalar(
        select(TraceSession)
        .join(
            EvalCaseAttempt,
            (EvalCaseAttempt.id == TraceSession.eval_case_attempt_id)
            & (EvalCaseAttempt.tenant_id == TraceSession.tenant_id),
        )
        .join(
            EvalCaseRun,
            (EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            & (EvalCaseRun.tenant_id == EvalCaseAttempt.tenant_id),
        )
        .join(
            EvalRun,
            (EvalRun.id == EvalCaseRun.eval_run_id)
            & (EvalRun.tenant_id == EvalCaseRun.tenant_id),
        )
        .where(
            TraceSession.id == session_id,
            TraceSession.tenant_id == tenant_id,
            EvalRun.project_id == project_id,
        )
    )
    if trace_session is None:
        raise TraceQueryError(404, "trace_session_not_found", "轨迹会话不存在")
    return trace_session


def _span_or_404(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    span_id: str,
) -> TraceSpan:
    span = session.scalar(
        select(TraceSpan)
        .join(
            TraceSession,
            (TraceSession.id == TraceSpan.session_id)
            & (TraceSession.tenant_id == TraceSpan.tenant_id),
        )
        .join(
            EvalCaseAttempt,
            (EvalCaseAttempt.id == TraceSession.eval_case_attempt_id)
            & (EvalCaseAttempt.tenant_id == TraceSession.tenant_id),
        )
        .join(
            EvalCaseRun,
            (EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            & (EvalCaseRun.tenant_id == EvalCaseAttempt.tenant_id),
        )
        .join(
            EvalRun,
            (EvalRun.id == EvalCaseRun.eval_run_id)
            & (EvalRun.tenant_id == EvalCaseRun.tenant_id),
        )
        .where(
            TraceSpan.id == span_id,
            TraceSpan.tenant_id == tenant_id,
            EvalRun.project_id == project_id,
        )
    )
    if span is None:
        raise TraceQueryError(404, "trace_span_not_found", "轨迹节点不存在")
    return span


def _span_body(
    span: TraceSpan,
    *,
    role: str,
    include_parent: bool = True,
) -> dict[str, Any]:
    thinking_hidden = span.kind == "THINKING" and role != "admin"
    content_available = bool(span.content_object_key)
    return {
        "id": span.id,
        "session_id": span.session_id,
        "parent_span_id": span.parent_span_id if include_parent else None,
        "native_event_id": span.native_event_id,
        "ordinal": span.ordinal,
        "kind": span.kind,
        "name": redact_text(span.name, limit=300) if span.name else None,
        "status": span.status,
        "started_at": iso_utc(span.started_at),
        "ended_at": iso_utc(span.ended_at),
        "duration_ms": span.duration_ms,
        "attributes": _sanitize(span.attributes_json or {}),
        "input_tokens": span.input_tokens,
        "output_tokens": span.output_tokens,
        "cost": str(span.cost) if span.cost is not None else None,
        "content_preview": (
            None
            if thinking_hidden
            else redact_text(span.content_preview, limit=2000)
            if span.content_preview
            else None
        ),
        "content_available": content_available,
        "content_hidden": thinking_hidden,
        "hidden_reason": "thinking_requires_admin" if thinking_hidden else None,
    }


def _session_quality(
    sessions: list[TraceSession], parent_ids: set[str]
) -> dict[str, Any]:
    unlinked = sorted(
        session.id for session in sessions if session.status == "unlinked"
    )
    orphan_parents = sorted(
        {
            session.parent_session_id
            for session in sessions
            if session.parent_session_id is not None
            and session.parent_session_id not in parent_ids
        }
    )
    return {
        "session_statuses": {
            status: sum(1 for session in sessions if session.status == status)
            for status in TRACE_SESSION_STATUSES
        },
        "unlinked_session_ids": unlinked,
        "orphan_parent_ids": orphan_parents,
        "has_unlinked_sessions": bool(unlinked),
        "has_orphan_sessions": bool(orphan_parents),
    }


def _file_quality(files: list[TraceFile]) -> dict[str, Any]:
    return {
        "file_statuses": {
            status: sum(1 for item in files if item.status == status)
            for status in ("pending", "parsing", "ready", "degraded", "failed")
        },
        "issues": [
            {
                "trace_file_id": item.id,
                "logical_path": (
                    redact_text(item.logical_path, limit=512)
                    if item.logical_path
                    else None
                ),
                "status": item.status,
                "error_code": item.error_code,
                "error_summary": (
                    redact_text(item.error_summary, limit=1000)
                    if item.error_summary
                    else None
                ),
                "line_no": item.line_no,
            }
            for item in files
            if item.status in {"degraded", "failed"} or item.error_code
        ],
    }


def _session_body(
    session: TraceSession,
    *,
    children: list[TraceSession],
    stats: dict[str, Any],
) -> dict[str, Any]:
    evidence = (
        _sanitize(dict(session.relation_evidence_json))
        if isinstance(session.relation_evidence_json, dict)
        else {}
    )
    return {
        "id": session.id,
        "native_session_id": session.native_session_id,
        "native_session_key": session.native_session_key,
        "root_session_id": session.root_session_id,
        "parent_session_id": session.parent_session_id,
        "agent_id": session.agent_id,
        "agent_name": session.agent_name,
        "runtime_key": session.runtime_key,
        "parser_version": session.parser_version,
        "status": session.status,
        "relation_evidence": evidence,
        "started_at": iso_utc(session.started_at),
        "ended_at": iso_utc(session.ended_at),
        "ordinal": session.ordinal,
        "span_count": stats.get("span_count", 0),
        "input_tokens": stats.get("input_tokens"),
        "output_tokens": stats.get("output_tokens"),
        "cost": stats.get("cost"),
        "children": [
            _session_body(child, children=[], stats={})
            for child in sorted(children, key=lambda row: (row.ordinal, row.id))
        ],
    }


def attempt_trace(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    case_run_id: str,
    attempt_no: int | None = None,
) -> dict[str, Any]:
    case_run = session.scalar(
        select(EvalCaseRun)
        .join(
            EvalRun,
            (EvalRun.id == EvalCaseRun.eval_run_id)
            & (EvalRun.tenant_id == EvalCaseRun.tenant_id),
        )
        .where(
            EvalCaseRun.id == case_run_id,
            EvalCaseRun.tenant_id == tenant_id,
            EvalRun.project_id == project_id,
        )
    )
    if case_run is None:
        raise TraceQueryError(404, "eval_case_run_not_found", "评测用例不存在")

    statement = select(EvalCaseAttempt).where(
        EvalCaseAttempt.eval_case_run_id == case_run.id,
        EvalCaseAttempt.tenant_id == tenant_id,
    )
    if attempt_no is None:
        statement = statement.order_by(
            EvalCaseAttempt.attempt_no.desc(), EvalCaseAttempt.id.desc()
        )
        attempt = session.scalars(statement.limit(1)).first()
    else:
        attempt = session.scalars(
            statement.where(EvalCaseAttempt.attempt_no == attempt_no)
        ).first()
    if attempt is None:
        if attempt_no is None:
            return {
                "case_run_id": case_run.id,
                "trace_status": case_run.trace_status,
                "attempt": None,
                "files": [],
                "sessions": [],
                "stats": {"session_count": 0, "span_count": 0},
                "quality": {
                    "files": _file_quality([]),
                    "sessions": _session_quality([], set()),
                    "clock_comparable": True,
                },
                "parser_versions": [],
            }
        raise TraceQueryError(404, "attempt_not_found", "用例执行尝试不存在")

    files = list(
        session.scalars(
            select(TraceFile)
            .where(
                TraceFile.eval_case_attempt_id == attempt.id,
                TraceFile.tenant_id == tenant_id,
            )
            .order_by(TraceFile.ordinal, TraceFile.id)
        )
    )
    sessions = list(
        session.scalars(
            select(TraceSession)
            .where(
                TraceSession.eval_case_attempt_id == attempt.id,
                TraceSession.tenant_id == tenant_id,
            )
            .order_by(TraceSession.ordinal, TraceSession.id)
        )
    )
    session_ids = [item.id for item in sessions]
    span_rows: dict[str, Any] = {}
    if session_ids:
        rows = session.execute(
            select(
                TraceSpan.session_id,
                func.count(TraceSpan.id),
                func.sum(TraceSpan.input_tokens),
                func.sum(TraceSpan.output_tokens),
                func.sum(TraceSpan.cost),
                func.min(TraceSpan.started_at),
                func.max(TraceSpan.ended_at),
            )
            .where(
                TraceSpan.tenant_id == tenant_id,
                TraceSpan.session_id.in_(session_ids),
            )
            .group_by(TraceSpan.session_id)
        )
        span_rows = {
            row[0]: {
                "span_count": int(row[1] or 0),
                "input_tokens": int(row[2]) if row[2] is not None else None,
                "output_tokens": int(row[3]) if row[3] is not None else None,
                "cost": str(row[4]) if row[4] is not None else None,
                "started_at": row[5],
                "ended_at": row[6],
            }
            for row in rows
        }

    by_parent: dict[str | None, list[TraceSession]] = defaultdict(list)
    known_ids = set(session_ids)
    for item in sessions:
        parent_id = (
            item.parent_session_id
            if item.parent_session_id in known_ids
            else None
        )
        by_parent[parent_id].append(item)

    seen: set[str] = set()

    def build(parent_id: str | None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in sorted(
            by_parent.get(parent_id, []), key=lambda row: (row.ordinal, row.id)
        ):
            if item.id in seen:
                continue
            seen.add(item.id)
            body = _session_body(
                item, children=[], stats=span_rows.get(item.id, {})
            )
            body["children"] = build(item.id)
            result.append(body)
        return result

    tree = build(None)
    # A corrupt/cyclic parent chain must not make sessions disappear silently.
    leftovers = [item for item in sessions if item.id not in seen]
    for item in leftovers:
        body = _session_body(item, children=[], stats=span_rows.get(item.id, {}))
        body["cycle_detected"] = True
        tree.append(body)

    clock_comparable = all(
        not (
            isinstance(stats.get("started_at"), datetime)
            and isinstance(stats.get("ended_at"), datetime)
            and stats["ended_at"] < stats["started_at"]
        )
        for stats in span_rows.values()
    )
    total_spans = sum(int(stats["span_count"]) for stats in span_rows.values())
    total_input = sum(
        int(stats["input_tokens"])
        for stats in span_rows.values()
        if stats.get("input_tokens") is not None
    )
    total_output = sum(
        int(stats["output_tokens"])
        for stats in span_rows.values()
        if stats.get("output_tokens") is not None
    )
    return {
        "case_run_id": case_run.id,
        "trace_status": case_run.trace_status,
        "attempt": {
            "id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "status": attempt.status,
            "phase": attempt.phase,
            "started_at": iso_utc(attempt.started_at),
            "ended_at": iso_utc(attempt.ended_at),
            "error_code": attempt.error_code,
            "error_summary": (
                redact_text(attempt.error_summary, limit=1000)
                if attempt.error_summary
                else None
            ),
        },
        "files": [
            {
                "id": item.id,
                "logical_path": (
                    redact_text(item.logical_path, limit=512)
                    if item.logical_path
                    else None
                ),
                "artifact_kind": item.artifact_kind,
                "native_format": item.native_format,
                "format_version": item.format_version,
                "parser_version": item.parser_version,
                "runtime_key": item.runtime_key,
                "runtime_version": item.runtime_version,
                "status": item.status,
                "detected_format": item.detected_format,
                "sha256": item.sha256,
                "size": item.size,
                "error_code": item.error_code,
                "error_summary": (
                    redact_text(item.error_summary, limit=1000)
                    if item.error_summary
                    else None
                ),
                "line_no": item.line_no,
            }
            for item in files
        ],
        "sessions": tree,
        "stats": {
            "session_count": len(sessions),
            "span_count": total_spans,
            "input_tokens": total_input,
            "output_tokens": total_output,
        },
        "quality": {
            "files": _file_quality(files),
            "sessions": _session_quality(sessions, known_ids),
            "clock_comparable": clock_comparable,
        },
        "parser_versions": sorted({item.parser_version for item in files}),
    }


def session_spans(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    session_id: str,
    role: str,
    cursor: str | None,
    limit: int,
    kind: str | None,
) -> dict[str, Any]:
    trace_session = _session_or_404(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        session_id=session_id,
    )
    marker = _decode_cursor(session_id, cursor)
    statement = select(TraceSpan).where(
        TraceSpan.tenant_id == tenant_id,
        TraceSpan.session_id == trace_session.id,
    )
    if marker is not None:
        ordinal, span_id = marker
        statement = statement.where(
            or_(
                TraceSpan.ordinal > ordinal,
                (TraceSpan.ordinal == ordinal) & (TraceSpan.id > span_id),
            )
        )
    if kind is not None:
        statement = statement.where(TraceSpan.kind == kind)
    rows = list(
        session.scalars(
            statement.order_by(TraceSpan.ordinal, TraceSpan.id).limit(limit + 1)
        )
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        _encode_cursor(trace_session.id, page[-1].ordinal, page[-1].id)
        if has_more and page
        else None
    )
    total_statement = select(func.count(TraceSpan.id)).where(
        TraceSpan.tenant_id == tenant_id,
        TraceSpan.session_id == trace_session.id,
    )
    if kind is not None:
        total_statement = total_statement.where(TraceSpan.kind == kind)
    total = int(session.scalar(total_statement) or 0)
    return {
        "session": {
            "id": trace_session.id,
            "eval_case_attempt_id": trace_session.eval_case_attempt_id,
            "agent_name": trace_session.agent_name,
            "agent_id": trace_session.agent_id,
            "status": trace_session.status,
            "parser_version": trace_session.parser_version,
        },
        "items": [_span_body(span, role=role) for span in page],
        "page_info": {
            "limit": limit,
            "total": total,
            "has_more": has_more,
            "next_cursor": next_cursor,
        },
    }


def span_detail(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    span_id: str,
    role: str,
) -> dict[str, Any]:
    span = _span_or_404(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        span_id=span_id,
    )
    body = _span_body(span, role=role)
    if span.parent_span_id:
        parent = session.scalar(
            select(TraceSpan.id).where(
                TraceSpan.id == span.parent_span_id,
                TraceSpan.tenant_id == tenant_id,
            )
        )
        body["parent_missing"] = parent is None
    else:
        body["parent_missing"] = False
    return body


def trace_file_for_download(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    file_id: str,
) -> TraceFile:
    trace_file = session.scalar(
        select(TraceFile)
        .join(
            EvalCaseAttempt,
            (EvalCaseAttempt.id == TraceFile.eval_case_attempt_id)
            & (EvalCaseAttempt.tenant_id == TraceFile.tenant_id),
        )
        .join(
            EvalCaseRun,
            (EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            & (EvalCaseRun.tenant_id == EvalCaseAttempt.tenant_id),
        )
        .join(
            EvalRun,
            (EvalRun.id == EvalCaseRun.eval_run_id)
            & (EvalRun.tenant_id == EvalCaseRun.tenant_id),
        )
        .where(
            TraceFile.id == file_id,
            TraceFile.tenant_id == tenant_id,
            EvalRun.project_id == project_id,
        )
    )
    if trace_file is None:
        raise TraceQueryError(404, "trace_file_not_found", "轨迹原件不存在")
    return trace_file


def span_content_for_download(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    span_id: str,
) -> TraceSpan:
    span = _span_or_404(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        span_id=span_id,
    )
    if not span.content_object_key:
        raise TraceQueryError(404, "trace_content_not_found", "轨迹节点没有外置内容")
    return span


__all__ = [
    "TraceQueryError",
    "attempt_trace",
    "session_spans",
    "span_detail",
    "span_content_for_download",
    "trace_file_for_download",
]

"""Signed self-evolution session envelope endpoint (M9, issue #263).

``GET /api/v1/self-evolution/sessions/{session_id}`` answers the harness-
evolution trajectory pull: one JSON envelope with the runtime identity, a
one-level childSessionIds list and the session's normalized NDJSON
trajectory. There is no Bearer credential — the short-lived per-report
HMAC in the query string (``run``/``exp``/``sig``) is the authorization, so a leaked
URL stops working when it expires.

Limits mirror the HE pull side (64 MiB total, 1 MiB per line, 1M lines) and
overshooting any of them fails with a stable 413 code instead of truncating:
the consumer must never silently receive a partial trajectory.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..models import EvalCaseAttempt, EvalCaseRun, TraceFile, TraceSession
from ..services import self_evolution as self_evolution_service
from ..services.evaluations import iso_utc
from ..services.object_store import (
    ObjectMissing,
    ObjectStoreError,
    object_store_from_settings,
)
from .errors import error_detail

router = APIRouter(tags=["self-evolution"])

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUPPORTED_RUNTIMES = ("claude-code", "claude_code")
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_LINES = 1_000_000


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=error_detail(code, message))


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _verify_signature(
    session_id: str,
    run_id: str | None,
    exp: str | None,
    sig: str | None,
    settings: Settings,
    now: datetime,
) -> HTTPException | None:
    key = settings.self_evolution_session_signing_key
    if not key:
        return _error(
            503,
            "self_evolution_not_configured",
            "自进化会话签名密钥未配置",
        )
    # Everything malformed collapses into the same 401 envelope: the grant is
    # one opaque signed URL, so callers get no oracle beyond valid/invalid.
    try:
        exp_value = int(exp) if exp is not None else None
    except ValueError:
        exp_value = None
    if (
        exp_value is None
        or sig is None
        or run_id is None
        or not SESSION_ID_PATTERN.match(session_id)
        or not SESSION_ID_PATTERN.match(run_id)
        or exp_value < 0
    ):
        return _error(
            401,
            "self_evolution_signature_invalid",
            "会话下载签名无效",
        )
    if exp_value < int(now.timestamp()):
        return _error(
            401,
            "self_evolution_signature_expired",
            "会话下载签名已过期",
        )
    if not self_evolution_service.session_signature_matches(key, run_id, exp_value, sig):
        return _error(
            401,
            "self_evolution_signature_invalid",
            "会话下载签名无效",
        )
    return None


def _verify_run_scope(
    session: Session, trace_session: TraceSession, run_id: str
) -> HTTPException | None:
    """The grant is scoped to one eval run: session must belong to that run.

    A mismatch — including unlinked sessions with no attempt ownership —
    answers with the same stable invalid-signature 401 instead of a distinct
    error, so the endpoint leaks nothing about other runs' sessions.
    """

    if not trace_session.eval_case_attempt_id:
        return _error(
            401,
            "self_evolution_signature_invalid",
            "会话下载签名无效",
        )
    attempt = session.get(EvalCaseAttempt, trace_session.eval_case_attempt_id)
    case_run = (
        session.get(EvalCaseRun, attempt.eval_case_run_id) if attempt is not None else None
    )
    if case_run is None or case_run.eval_run_id != run_id:
        return _error(
            401,
            "self_evolution_signature_invalid",
            "会话下载签名无效",
        )
    return None


def _load_trajectory(
    trace_file: TraceFile, settings: Settings
) -> tuple[str, int]:
    """Read + validate the session's raw NDJSON object.

    Returns ``(content, line_count)``; raises the mapped HTTP error when the
    object is unavailable or any HE pull-side limit is exceeded.
    """

    store = object_store_from_settings(settings)
    if store is None:
        raise _error(
            503,
            "self_evolution_object_store_unavailable",
            "对象存储未配置，无法读取会话轨迹",
        )
    try:
        try:
            size = store.head_object(trace_file.object_key)
        except ObjectMissing as exc:
            raise _error(
                404,
                "self_evolution_trajectory_missing",
                "会话轨迹对象不存在",
            ) from exc
        if size > MAX_TOTAL_BYTES:
            raise _error(
                413,
                "self_evolution_trajectory_too_large",
                f"会话轨迹超过 64MiB 上限（{size} 字节）",
            )
        try:
            data = store.get_bytes(trace_file.object_key)
        except ObjectMissing as exc:
            raise _error(
                404,
                "self_evolution_trajectory_missing",
                "会话轨迹对象不存在",
            ) from exc
    except ObjectStoreError as exc:
        raise _error(
            502,
            "self_evolution_object_store_error",
            "对象存储读取失败",
        ) from exc

    if len(data) > MAX_TOTAL_BYTES:
        raise _error(
            413,
            "self_evolution_trajectory_too_large",
            f"会话轨迹超过 64MiB 上限（{len(data)} 字节）",
        )
    content = data.decode("utf-8", errors="replace").rstrip("\n")
    lines = content.split("\n") if content else []
    if len(lines) > MAX_LINES:
        raise _error(
            413,
            "self_evolution_trajectory_too_many_lines",
            f"会话轨迹超过 {MAX_LINES:,} 行上限（{len(lines)} 行）",
        )
    for line in lines:
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise _error(
                413,
                "self_evolution_trajectory_line_too_long",
                "会话轨迹存在超过 1MiB 的单行",
            )
    return content, len(lines)


@router.get("/api/v1/self-evolution/sessions/{session_id}")
def get_session_envelope(
    session_id: str,
    run: Annotated[str | None, Query(max_length=64)] = None,
    exp: Annotated[str | None, Query(max_length=32)] = None,
    sig: Annotated[str | None, Query(max_length=128)] = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    now = _now(None)
    failure = _verify_signature(session_id, run, exp, sig, settings, now)
    if failure is not None:
        raise failure

    trace_session = session.scalar(
        select(TraceSession)
        .where(TraceSession.native_session_id == session_id)
        .order_by(TraceSession.created_at.desc(), TraceSession.id)
        .limit(1)
    )
    if trace_session is None:
        raise _error(404, "self_evolution_session_not_found", "会话不存在")

    failure = _verify_run_scope(session, trace_session, run)
    if failure is not None:
        raise failure

    if (
        trace_session.runtime_key not in SUPPORTED_RUNTIMES
        and not trace_session.parser_version.startswith("claude-code")
    ):
        raise _error(
            404,
            "self_evolution_runtime_unsupported",
            "该会话的运行时尚不支持自进化会话包下载",
        )

    trace_file = session.get(TraceFile, trace_session.trace_file_id)
    if trace_file is None:
        raise _error(
            404,
            "self_evolution_trajectory_missing",
            "会话轨迹文件记录不存在",
        )

    agent_kind = "main" if trace_session.parent_session_id is None else "subagent"
    parent_session_id = None
    if trace_session.parent_session_id is not None:
        parent = session.get(TraceSession, trace_session.parent_session_id)
        if parent is not None:
            parent_session_id = parent.native_session_id
    child_session_ids = list(
        session.execute(
            select(TraceSession.native_session_id)
            .where(
                TraceSession.parent_session_id == trace_session.id,
                TraceSession.eval_case_attempt_id == trace_session.eval_case_attempt_id,
            )
            .order_by(TraceSession.ordinal, TraceSession.id)
        ).scalars()
    )

    # agentMeta must stay inside harness-evolution's closed Claude Code
    # metadata slice (extra="forbid"): only keys of that vocabulary survive.
    # agent_name maps to ``name``; agent_id/root_session_id have no Claude
    # meta.json counterpart and are dropped rather than risking a 422 on the
    # pull side.
    agent_meta: dict = {}
    if trace_session.agent_name:
        agent_meta["name"] = trace_session.agent_name

    content, line_count = _load_trajectory(trace_file, settings)

    return {
        "schemaVersion": 1,
        "sessionId": session_id,
        "runtime": "claude-code",
        "runtimeVersion": trace_session.parser_version,
        "agentKind": agent_kind,
        "parentSessionId": parent_session_id,
        "childSessionIds": child_session_ids,
        "agentMeta": agent_meta or None,
        "trajectory": {
            "format": "ndjson",
            "lineCount": line_count,
            "content": content,
        },
        "updatedAt": iso_utc(trace_session.updated_at),
    }

"""Idempotent native-trace ingestion pipeline (M6-02, design 08 §5).

The completion callback records the manifest and enqueues one ``trace_file``
row per native object. A worker (or a test) claims a pending row with a lease,
downloads the immutable object, verifies size and digest, parses it with a
versioned parser, and replaces only that file's derived tree inside a
savepoint. A parser upgrade creates new trace_file rows and replaces the
attempt's derived tree while keeping the old provenance rows.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..config import Settings, get_settings
from ..models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun
from ..models.trace import TraceFile, TraceSession, TraceSpan
from .object_store import ObjectMissing, ObjectStoreError, object_store_from_settings
from .trace_manifest import trace_objects, trace_status_for
from .trace_parsers import (
    DEFAULT_REGISTRY,
    ParsedFile,
    ParseError,
    ParseInput,
    ParserRegistry,
    detect_format,
)
from .trace_redaction import redact_text

EVENT_TYPE = "trace.ingestion.updated"
INGESTIBLE_KINDS = ("trace", "trace_metadata")
MAX_EVENT_FILES = 16
MAX_EVENT_ERRORS = 16
EVENT_INSERT_RETRIES = 5
VALID_RELATION_CONFIDENCE = ("authoritative", "derived")
LEASE_OWNER_LIMIT = 64


class LeaseLost(RuntimeError):
    """The worker no longer owns the trace_file row it is processing."""


def _new_worker_id() -> str:
    return f"worker-{uuid4().hex[:16]}"


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


def _run(session: Session, attempt: EvalCaseAttempt) -> EvalRun:
    case_run = session.get(EvalCaseRun, attempt.eval_case_run_id)
    if case_run is None:  # pragma: no cover - FK guarantees the parent
        raise LookupError(f"case run 不存在：{attempt.eval_case_run_id}")
    run = session.get(EvalRun, case_run.eval_run_id)
    if run is None:  # pragma: no cover - FK guarantees the parent
        raise LookupError(f"评测不存在：{case_run.eval_run_id}")
    return run


def _manifest(attempt: EvalCaseAttempt) -> dict:
    return dict((attempt.artifact_manifest_json or {}).get("trace_manifest") or {})


def _trace_items(attempt: EvalCaseAttempt) -> list[dict]:
    manifest = _manifest(attempt)
    items = trace_objects(manifest) if manifest else []
    if not items:
        items = [
            item
            for item in (attempt.artifact_manifest_json or {}).get("objects") or []
            if isinstance(item, dict)
        ]
    return [
        item
        for item in items
        if item.get("artifact_kind") in INGESTIBLE_KINDS and item.get("object_key")
    ]


def _item_key(item: dict) -> tuple[str, str]:
    return (item.get("logical_path") or "", item.get("sha256") or "")


def _runtime_keys(run: EvalRun, manifest: dict, item: dict) -> tuple[str, str | None]:
    runtime_key = run.runtime_key or manifest.get("runtime_key") or "unknown"
    runtime_version = (
        item.get("runtime_version")
        or manifest.get("runtime_version")
        or run.runtime_version
    )
    return runtime_key, runtime_version


def _resolve_parser(
    registry: ParserRegistry,
    *,
    run: EvalRun,
    manifest: dict,
    item: dict,
    parser_version: str | None,
):
    detected = detect_format(
        logical_path=item.get("logical_path"),
        content_type=item.get("content_type"),
        native_format=item.get("native_format"),
    )
    runtime_key, _ = _runtime_keys(run, manifest, item)
    parser = registry.resolve(
        runtime_key=runtime_key,
        detected_format=detected,
        format_version=item.get("format_version"),
        parser_version=parser_version,
    )
    return detected, parser


def _ensure_trace_file(
    session: Session,
    *,
    attempt: EvalCaseAttempt,
    case_run: EvalCaseRun,
    run: EvalRun,
    manifest: dict,
    item: dict,
    detected: str,
    parser,
    ordinal: int,
    now: datetime,
) -> TraceFile:
    runtime_key, runtime_version = _runtime_keys(run, manifest, item)
    existing = session.scalar(
        select(TraceFile).where(
            TraceFile.eval_case_attempt_id == attempt.id,
            TraceFile.sha256 == item["sha256"],
            TraceFile.parser_version == parser.parser_version,
        )
    )
    if existing is not None:
        existing.logical_path = item.get("logical_path") or existing.logical_path
        existing.artifact_kind = item.get("artifact_kind") or existing.artifact_kind
        existing.object_key = item.get("object_key") or existing.object_key
        existing.size = (
            int(item["size"]) if item.get("size") is not None else existing.size
        )
        existing.runtime_key = runtime_key
        existing.runtime_version = runtime_version or existing.runtime_version
        existing.native_format = item.get("native_format") or existing.native_format
        existing.format_version = item.get("format_version") or existing.format_version
        existing.detected_format = existing.detected_format or detected
        existing.ordinal = ordinal
        return existing

    record = TraceFile(
        tenant_id=attempt.tenant_id,
        eval_case_attempt_id=attempt.id,
        eval_case_run_id=case_run.id,
        runtime_key=runtime_key,
        runtime_version=runtime_version,
        native_format=item.get("native_format"),
        format_version=item.get("format_version"),
        parser_version=parser.parser_version,
        logical_path=item.get("logical_path"),
        artifact_kind=item["artifact_kind"],
        object_key=item["object_key"],
        sha256=item["sha256"],
        size=int(item.get("size") or 0),
        detected_format=detected,
        status="pending",
        ordinal=ordinal,
    )
    try:
        with session.begin_nested():
            session.add(record)
            session.flush()
    except IntegrityError:
        record = session.scalar(
            select(TraceFile).where(
                TraceFile.eval_case_attempt_id == attempt.id,
                TraceFile.sha256 == item["sha256"],
                TraceFile.parser_version == parser.parser_version,
            )
        )
        if record is None:  # pragma: no cover - constraint was ours
            raise
    return record


def enqueue_attempt(
    session: Session,
    attempt: EvalCaseAttempt,
    *,
    now: datetime | None = None,
    parser_registry: ParserRegistry | None = None,
) -> dict[str, int]:
    """Create/patch pending trace_file rows without touching derived results."""

    now = now or datetime.now(UTC)
    registry = parser_registry or DEFAULT_REGISTRY
    items = sorted(_trace_items(attempt), key=_item_key)
    if not items:
        return {"enqueued": 0, "existing": 0}
    case_run = session.get(EvalCaseRun, attempt.eval_case_run_id)
    if case_run is None:  # pragma: no cover
        return {"enqueued": 0, "existing": 0}
    run = _run(session, attempt)
    manifest = _manifest(attempt)
    enqueued = 0
    existing = 0
    for ordinal, item in enumerate(items):
        detected, parser = _resolve_parser(
            registry, run=run, manifest=manifest, item=item, parser_version=None
        )
        before = session.scalar(
            select(TraceFile.id).where(
                TraceFile.eval_case_attempt_id == attempt.id,
                TraceFile.sha256 == item["sha256"],
                TraceFile.parser_version == parser.parser_version,
            )
        )
        _ensure_trace_file(
            session,
            attempt=attempt,
            case_run=case_run,
            run=run,
            manifest=manifest,
            item=item,
            detected=detected,
            parser=parser,
            ordinal=ordinal,
            now=now,
        )
        if before is None:
            enqueued += 1
        else:
            existing += 1
    if enqueued:
        capture = manifest.get("capture_status")
        if capture in ("partial", "incomplete"):
            status = "incomplete"
        elif capture == "failed":
            status = "failed"
        elif capture == "degraded":
            status = "degraded"
        else:
            # Parsing has not run yet; a fresh attempt must not advertise a
            # ready trace merely because the callback wrote the manifest.
            status = "pending"
        _set_case_trace_status(
            session, case_run=case_run, attempt=attempt, status=status
        )
    session.flush()
    return {"enqueued": enqueued, "existing": existing}


def _claim(
    session: Session,
    trace_file_id: str,
    *,
    now: datetime,
    lease_seconds: float,
    owner: str,
    allow_failed: bool = False,
    allow_reparse: bool = False,
) -> bool:
    lease_until = now + timedelta(seconds=lease_seconds)
    claimable = [
        TraceFile.status == "pending",
        (TraceFile.status == "parsing")
        & or_(TraceFile.lease_until.is_(None), TraceFile.lease_until < now),
    ]
    if allow_failed:
        # Explicit repair only; normal sweeps must never retry terminal rows.
        claimable.append(TraceFile.status == "failed")
    if allow_reparse:
        # Atomic upgrades stage every file, including rows that were already
        # terminal under the same parser version in a mixed-version batch.
        claimable.extend(
            [TraceFile.status == "ready", TraceFile.status == "degraded"]
        )
    result = session.execute(
        update(TraceFile)
        .where(
            TraceFile.id == trace_file_id,
            or_(*claimable),
        )
        .values(
            status="parsing",
            lease_until=lease_until,
            lease_owner=owner[:LEASE_OWNER_LIMIT],
            error_code=None,
            error_summary=None,
            line_no=None,
            ended_at=None,
            started_at=func.coalesce(TraceFile.started_at, now),
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return bool(result.rowcount)


def _finish_lease(
    session: Session,
    trace_file_id: str,
    *,
    owner: str | None,
    status: str,
    error_code: str | None,
    error_summary: str | None,
    line_no: int | None,
    now: datetime,
) -> bool:
    """Close a claimed row only while ``owner`` still holds the lease."""

    conditions = [TraceFile.id == trace_file_id, TraceFile.status == "parsing"]
    if owner is not None:
        conditions.append(TraceFile.lease_owner == owner[:LEASE_OWNER_LIMIT])
    result = session.execute(
        update(TraceFile)
        .where(*conditions)
        .values(
            status=status,
            error_code=(error_code or "")[:64] or None,
            error_summary=(
                redact_text(error_summary, limit=1000) if error_summary else None
            ),
            line_no=line_no,
            ended_at=now,
            lease_until=None,
            lease_owner=None,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)


def _mark_failed(
    trace_file: TraceFile,
    *,
    code: str,
    summary: str,
    line_no: int | None,
    now: datetime,
) -> None:
    trace_file.status = "failed"
    trace_file.error_code = (code or "parse_failed")[:64]
    trace_file.error_summary = redact_text(summary, limit=1000)
    trace_file.line_no = line_no
    trace_file.ended_at = now
    trace_file.lease_until = None
    trace_file.lease_owner = None


@dataclass
class _StagedTrace:
    trace_file_id: str
    parser_version: str
    parsed: ParsedFile | None
    status: str
    error_code: str | None = None
    error_summary: str | None = None
    line_no: int | None = None
    lease_owner: str | None = None
    lease_lost: bool = False


def _parsed_session_key(parsed_session) -> str:
    """Stable attempt-local key: raw native id plus native agent identity."""

    agent = parsed_session.agent_id or "root"
    return f"{parsed_session.native_session_id}:{agent}"[:400]


def _validate_relation_evidence(parsed: ParsedFile) -> None:
    """Reject unknown confidence values and unversioned derived relations."""

    problems: list[str] = []
    for parsed_session in parsed.sessions:
        evidence = dict(parsed_session.relation_evidence or {})
        confidence = evidence.get("confidence")
        if confidence is not None and confidence not in VALID_RELATION_CONFIDENCE:
            problems.append(
                f"session {parsed_session.native_session_id} 的 relation confidence 非法：{confidence}"
            )
        elif confidence == "derived" and not (
            isinstance(evidence.get("rule_version"), str)
            and evidence["rule_version"]
        ):
            problems.append(
                f"session {parsed_session.native_session_id} 的 derived 关系缺少 rule_version"
            )
    if not problems:
        return
    for parsed_session in parsed.sessions:
        parsed_session.status = "degraded"
        evidence = dict(parsed_session.relation_evidence or {})
        evidence["validation_error"] = problems[0]
        parsed_session.relation_evidence = evidence
    parsed.status = "degraded"
    if not parsed.error_code:
        parsed.error_code = "invalid_relation_evidence"
        parsed.error_summary = problems[0]


def _renew_lease(
    session: Session,
    trace_file_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: float,
    owner: str | None = None,
) -> bool:
    """Renew a claimed file between download/parse stages.

    Long parses can exceed the configurable ``trace_ingestion_lease_seconds``;
    this moves the lease forward only while ``owner`` still matches. The worker
    loop also treats an expired lease as reclaimable, so a crashed parser
    cannot strand a file forever.
    """

    renewed_until = (now or datetime.now(UTC)) + timedelta(seconds=lease_seconds)
    conditions = [TraceFile.id == trace_file_id, TraceFile.status == "parsing"]
    if owner is not None:
        conditions.append(TraceFile.lease_owner == owner[:LEASE_OWNER_LIMIT])
    result = session.execute(
        update(TraceFile)
        .where(*conditions)
        .values(lease_until=renewed_until)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return bool(result.rowcount)


def _replace_file_tree(session: Session, trace_file_id: str) -> None:
    session_ids = select(TraceSession.id).where(TraceSession.trace_file_id == trace_file_id)
    span_ids = select(TraceSpan.id).where(TraceSpan.session_id.in_(session_ids))
    session.execute(
        update(TraceSpan)
        .where(TraceSpan.parent_span_id.in_(span_ids))
        .values(parent_span_id=None)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(TraceSession)
        .where(TraceSession.parent_session_id.in_(session_ids))
        .values(parent_session_id=None)
        .execution_options(synchronize_session=False)
    )
    session.execute(delete(TraceSpan).where(TraceSpan.session_id.in_(session_ids)))
    session.execute(delete(TraceSession).where(TraceSession.trace_file_id == trace_file_id))


def _replace_attempt_tree(session: Session, attempt_id: str) -> None:
    session_ids = select(TraceSession.id).where(
        TraceSession.eval_case_attempt_id == attempt_id
    )
    span_ids = select(TraceSpan.id).where(TraceSpan.session_id.in_(session_ids))
    session.execute(
        update(TraceSpan)
        .where(TraceSpan.parent_span_id.in_(span_ids))
        .values(parent_span_id=None)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(TraceSession)
        .where(TraceSession.parent_session_id.in_(session_ids))
        .values(parent_session_id=None)
        .execution_options(synchronize_session=False)
    )
    session.execute(delete(TraceSpan).where(TraceSpan.session_id.in_(session_ids)))
    session.execute(
        delete(TraceSession).where(TraceSession.eval_case_attempt_id == attempt_id)
    )


def _sort_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.max.replace(tzinfo=UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _assign_session_ordinals(
    staged_files: list[_StagedTrace], *, start: int = 0
) -> dict[tuple[str, int], int]:
    """Order sessions by (started_at, native_session_key) across the attempt."""

    entries = []
    for staged in staged_files:
        if staged.parsed is None:
            continue
        for index, parsed_session in enumerate(staged.parsed.sessions):
            entries.append(
                (
                    _sort_datetime(parsed_session.started_at),
                    _parsed_session_key(parsed_session),
                    staged.trace_file_id,
                    index,
                )
            )
    entries.sort()
    return {
        (trace_file_id, index): start + ordinal
        for ordinal, (_started, _key, trace_file_id, index) in enumerate(entries)
    }


def _parent_session(
    *,
    native_session_id: str,
    by_key: dict[str, TraceSession],
    by_native: dict[str, list[TraceSession]],
) -> TraceSession | None:
    exact = by_key.get(native_session_id)
    if exact is not None:
        return exact
    candidates = by_native.get(native_session_id) or []
    return candidates[0] if len(candidates) == 1 else None


def _persist_staged_tree(
    session: Session,
    staged_files: list[_StagedTrace],
    *,
    attempt_id: str,
    now: datetime,
    session_ordinals: dict[tuple[str, int], int],
) -> None:
    """Insert all parsed sessions/spans for the staged files in one transaction.

    The caller decides the transaction boundary. For an atomic parser upgrade
    this is called after deleting the old attempt tree inside the same
    transaction.
    """

    # Completing the lease is part of the same transaction as the derived tree.
    # If another worker reclaimed a file in the meantime, abort the whole
    # transaction instead of letting the old owner write a partial tree.
    for staged in staged_files:
        if staged.lease_owner is None:
            continue
        if not _finish_lease(
            session,
            staged.trace_file_id,
            owner=staged.lease_owner,
            status=staged.status,
            error_code=staged.error_code,
            error_summary=staged.error_summary,
            line_no=staged.line_no,
            now=now,
        ):
            raise LeaseLost(f"trace_file 租约已被回收：{staged.trace_file_id}")

    records: dict[tuple[str, int], TraceSession] = {}
    by_key: dict[str, TraceSession] = {}
    by_native: dict[str, list[TraceSession]] = {}
    parser_versions = {staged.parser_version for staged in staged_files}

    existing = list(
        session.scalars(
            select(TraceSession).where(
                TraceSession.eval_case_attempt_id == attempt_id,
                TraceSession.parser_version.in_(sorted(parser_versions)),
            )
        )
    )
    for record in existing:
        by_key[record.native_session_key] = record
        by_native.setdefault(record.native_session_id, []).append(record)

    for staged in staged_files:
        if staged.parsed is None:
            continue
        for index, parsed_session in enumerate(staged.parsed.sessions):
            key = _parsed_session_key(parsed_session)
            record = TraceSession(
                tenant_id=session.get(TraceFile, staged.trace_file_id).tenant_id,
                trace_file_id=staged.trace_file_id,
                eval_case_attempt_id=attempt_id,
                runtime_key=session.get(TraceFile, staged.trace_file_id).runtime_key,
                parser_version=staged.parser_version,
                native_session_id=parsed_session.native_session_id[:200],
                native_session_key=key,
                root_session_id=_clip(parsed_session.root_session_id, 200),
                agent_id=_clip(parsed_session.agent_id, 200),
                agent_name=_clip(parsed_session.agent_name, 200),
                status=parsed_session.status[:16],
                relation_evidence_json=dict(parsed_session.relation_evidence or {}),
                metadata_json=dict(parsed_session.metadata or {}),
                started_at=parsed_session.started_at,
                ended_at=parsed_session.ended_at,
                ordinal=session_ordinals[(staged.trace_file_id, index)],
            )
            session.add(record)
            records[(staged.trace_file_id, index)] = record
    session.flush()

    for record in records.values():
        by_key.setdefault(record.native_session_key, record)
        by_native.setdefault(record.native_session_id, []).append(record)

    for staged in staged_files:
        if staged.parsed is None:
            continue
        for index, parsed_session in enumerate(staged.parsed.sessions):
            record = records[(staged.trace_file_id, index)]
            if parsed_session.parent_native_session_id:
                parent = _parent_session(
                    native_session_id=parsed_session.parent_native_session_id,
                    by_key=by_key,
                    by_native=by_native,
                )
                if parent is None or parent.id == record.id:
                    if parent is None and record.status == "ready":
                        record.status = "unlinked"
                    if parent is None:
                        record.relation_evidence_json = {
                            **record.relation_evidence_json,
                            "type": "unresolved_parent",
                            "parent_native_session_id": parsed_session.parent_native_session_id,
                        }
                else:
                    record.parent_session_id = parent.id

    for staged in staged_files:
        if staged.parsed is None:
            continue
        for index, parsed_session in enumerate(staged.parsed.sessions):
            record = records[(staged.trace_file_id, index)]
            spans: dict[str, TraceSpan] = {}
            ordered_spans = sorted(
                parsed_session.spans,
                key=lambda parsed_span: (
                    parsed_span.ordinal,
                    parsed_span.native_event_id or "",
                ),
            )
            for span_ordinal, parsed_span in enumerate(ordered_spans):
                span = TraceSpan(
                    tenant_id=record.tenant_id,
                    session_id=record.id,
                    native_event_id=_clip(parsed_span.native_event_id, 200),
                    ordinal=span_ordinal,
                    kind=parsed_span.kind,
                    name=_clip(parsed_span.name, 300),
                    status=_clip(parsed_span.status, 32),
                    started_at=parsed_span.started_at,
                    ended_at=parsed_span.ended_at,
                    duration_ms=parsed_span.duration_ms,
                    attributes_json=dict(parsed_span.attributes or {}),
                    input_tokens=parsed_span.input_tokens,
                    output_tokens=parsed_span.output_tokens,
                    cost=parsed_span.cost,
                    content_preview=(
                        redact_text(parsed_span.content_preview, limit=2000)
                        if parsed_span.content_preview is not None
                        else None
                    ),
                    content_object_key=_clip(parsed_span.content_object_key, 1024),
                )
                session.add(span)
                if parsed_span.native_event_id:
                    spans[parsed_span.native_event_id] = span
            session.flush()
            for parsed_span in ordered_spans:
                if not parsed_span.parent_native_id:
                    continue
                parent = spans.get(parsed_span.parent_native_id)
                if parent is None:
                    continue
                span = spans.get(parsed_span.native_event_id or "")
                if span is not None:
                    span.parent_span_id = parent.id

    for staged in staged_files:
        row = session.get(TraceFile, staged.trace_file_id)
        row.detected_format = (
            staged.parsed.detected_format if staged.parsed is not None else row.detected_format
        )
        row.status = staged.status
        row.error_code = (staged.error_code or "")[:64] or None
        row.error_summary = (
            redact_text(staged.error_summary, limit=1000) if staged.error_summary else None
        )
        row.line_no = staged.line_no
        row.ended_at = now
        row.lease_until = None
        row.lease_owner = None
    session.flush()


def _relink_parents(session: Session, attempt_id: str) -> None:
    records = list(
        session.scalars(
            select(TraceSession).where(TraceSession.eval_case_attempt_id == attempt_id)
        )
    )
    by_key = {record.native_session_key: record for record in records}
    by_native: dict[str, list[TraceSession]] = {}
    for record in records:
        by_native.setdefault(record.native_session_id, []).append(record)
    for record in records:
        if record.parent_session_id is not None:
            continue
        parent_id = (record.relation_evidence_json or {}).get("parent_native_session_id")
        if not parent_id:
            continue
        parent = _parent_session(
            native_session_id=parent_id, by_key=by_key, by_native=by_native
        )
        if parent is not None and parent.id != record.id:
            record.parent_session_id = parent.id
            if record.status == "unlinked":
                record.status = "ready"
    session.flush()


def _load_and_parse(
    session: Session,
    *,
    trace_file: TraceFile,
    parser,
    store,
    now: datetime,
    lease_seconds: float,
    lease_owner: str,
) -> _StagedTrace:
    """Download, verify and parse one file without touching derived rows."""

    trace_file = session.get(TraceFile, trace_file.id)
    if trace_file is None:  # pragma: no cover - caller owns the row
        raise LookupError("trace_file 不存在")

    def lost() -> _StagedTrace:
        return _StagedTrace(
            trace_file_id=trace_file.id,
            parser_version=trace_file.parser_version,
            parsed=None,
            status="skipped",
            lease_owner=lease_owner,
            lease_lost=True,
        )

    def failed(code: str, summary: str, line_no: int | None = None) -> _StagedTrace:
        finished = _finish_lease(
            session,
            trace_file.id,
            owner=lease_owner,
            status="failed",
            error_code=code,
            error_summary=summary,
            line_no=line_no,
            now=now,
        )
        session.commit()
        if not finished:
            return lost()
        return _StagedTrace(
            trace_file_id=trace_file.id,
            parser_version=trace_file.parser_version,
            parsed=None,
            status="failed",
            error_code=code,
            error_summary=summary,
            line_no=line_no,
            lease_owner=lease_owner,
        )

    if store is None:
        return failed("object_store_unavailable", "对象存储未配置，无法读取轨迹原件")

    # Renew against the wall clock, not the sweep's start time: a long parse
    # must keep moving the lease forward.
    if not _renew_lease(
        session, trace_file.id, lease_seconds=lease_seconds, owner=lease_owner
    ):
        return lost()
    try:
        data = store.get_bytes(trace_file.object_key)
    except ObjectMissing:
        return failed("artifact_missing", "轨迹原件不存在")
    except ObjectStoreError:
        return failed("object_unavailable", "轨迹原件读取失败")
    if not _renew_lease(
        session, trace_file.id, lease_seconds=lease_seconds, owner=lease_owner
    ):
        return lost()

    if len(data) != trace_file.size:
        return failed(
            "size_mismatch",
            f"对象大小不一致：实际 {len(data)}，清单 {trace_file.size}",
        )
    digest = hashlib.sha256(data).hexdigest()
    if digest != trace_file.sha256:
        return failed("digest_mismatch", "对象 sha256 与清单不一致")

    lease_ok = True
    try:
        parsed = parser.parse(
            data,
            ParseInput(
                runtime_key=trace_file.runtime_key,
                runtime_version=trace_file.runtime_version,
                native_format=trace_file.native_format,
                format_version=trace_file.format_version,
                detected_format=trace_file.detected_format or "unknown",
                logical_path=trace_file.logical_path,
                object_key=trace_file.object_key,
                sha256=trace_file.sha256,
                content_type=None,
                size=trace_file.size,
                artifact_kind=trace_file.artifact_kind,
            ),
        )
    except ParseError as exc:
        return failed(exc.code, exc.summary, exc.line_no)
    except Exception as exc:  # noqa: BLE001 - isolate one file from the batch
        return failed("parser_error", f"解析器异常：{type(exc).__name__}")
    finally:
        lease_ok = _renew_lease(
            session, trace_file.id, lease_seconds=lease_seconds, owner=lease_owner
        )

    if not lease_ok:
        return lost()
    _validate_relation_evidence(parsed)
    if (
        not parsed.sessions
        and trace_file.artifact_kind != "trace_metadata"
        and parsed.error_code != "unknown_format_version"
    ):
        return failed(
            "empty_trace", "轨迹文件没有可用的 session", line_no=parsed.line_no
        )
    return _StagedTrace(
        trace_file_id=trace_file.id,
        parser_version=trace_file.parser_version,
        parsed=parsed,
        status=parsed.status,
        error_code=parsed.error_code,
        error_summary=parsed.error_summary,
        line_no=parsed.line_no,
        lease_owner=lease_owner,
    )


def _abort_upgrade(
    session: Session,
    *,
    staged_files: list[_StagedTrace],
    now: datetime,
) -> None:
    """Keep the old derived tree and close every new-version row."""

    for staged in staged_files:
        row = session.get(TraceFile, staged.trace_file_id)
        if row is None:  # pragma: no cover - row created by this attempt
            continue
        if staged.parsed is None:
            if staged.lease_owner is None or _finish_lease(
                session,
                row.id,
                owner=staged.lease_owner,
                status="failed",
                error_code=staged.error_code or "parser_upgrade_failed",
                error_summary=staged.error_summary or "parser 升级失败",
                line_no=staged.line_no,
                now=now,
            ):
                if staged.lease_owner is None:
                    _mark_failed(
                        row,
                        code=staged.error_code or "parser_upgrade_failed",
                        summary=staged.error_summary or "parser 升级失败",
                        line_no=staged.line_no,
                        now=now,
                    )
            continue
        if staged.lease_owner is None or _finish_lease(
            session,
            row.id,
            owner=staged.lease_owner,
            status="degraded",
            error_code="parser_upgrade_aborted",
            error_summary="parser 升级未完成，保留旧派生树",
            line_no=None,
            now=now,
        ):
            if staged.lease_owner is None:
                row.status = "degraded"
                row.error_code = "parser_upgrade_aborted"
                row.error_summary = "parser 升级未完成，保留旧派生树"
                row.ended_at = now
                row.lease_until = None
                row.lease_owner = None
    session.flush()


def set_case_trace_status(
    session: Session,
    *,
    case_run: EvalCaseRun,
    attempt: EvalCaseAttempt,
    status: str,
) -> bool:
    """Single trace-status write path; only the latest attempt owns the badge.

    The guard is a conditional UPDATE against the database row, not a Python
    comparison on a possibly stale ORM instance. A concurrent retry that
    advanced ``latest_attempt_no`` wins and the old attempt publishes only its
    attempt-scoped event.
    """

    result = session.execute(
        update(EvalCaseRun)
        .where(
            EvalCaseRun.id == case_run.id,
            EvalCaseRun.latest_attempt_no == attempt.attempt_no,
        )
        .values(trace_status=status)
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        return False
    # Keep the caller's identity map consistent without marking the column
    # dirty (a later unconditional flush must not bypass the guard).
    set_committed_value(case_run, "trace_status", status)
    return True


def _set_case_trace_status(
    session: Session,
    *,
    case_run: EvalCaseRun,
    attempt: EvalCaseAttempt,
    status: str,
) -> bool:
    """Backward-compatible alias for the unified latest-attempt guard."""

    return set_case_trace_status(
        session, case_run=case_run, attempt=attempt, status=status
    )


def has_ingestible_trace_objects(attempt: EvalCaseAttempt) -> bool:
    """Whether completion should enqueue parsing rather than report unlinked."""

    return bool(_trace_items(attempt))


def _next_session_ordinal(session: Session, attempt_id: str) -> int:
    current = session.scalar(
        select(func.max(TraceSession.ordinal)).where(
            TraceSession.eval_case_attempt_id == attempt_id
        )
    )
    return int(current or -1) + 1


def _trace_rows(
    session: Session, *, attempt_id: str, parser_versions: set[str]
) -> list[TraceFile]:
    if not parser_versions:
        return []
    return list(
        session.scalars(
            select(TraceFile)
            .where(
                TraceFile.eval_case_attempt_id == attempt_id,
                TraceFile.parser_version.in_(sorted(parser_versions)),
            )
            .order_by(TraceFile.ordinal, TraceFile.id)
            # The lock protocol requires the publisher to see committed row
            # state, not identity-map values from before another worker wrote.
            .execution_options(populate_existing=True)
        )
    )


def _derive_trace_status(
    rows: list[TraceFile],
    *,
    manifest: dict,
    expected: int,
) -> tuple[str, Counter[str], bool]:
    """Return (terminal status, counts, has_inflight_file).

    Precedence is fixed by design 08 §5: all files failed, incomplete capture,
    degraded files, then ready. ``inflight`` is reported separately because the
    publisher must not downgrade a ready tree while a peer is still parsing.
    """

    counts = Counter(row.status for row in rows)
    capture = manifest.get("capture_status")
    ready = counts["ready"]
    degraded = counts["degraded"]
    failed = counts["failed"]
    inflight = counts["pending"] + counts["parsing"]

    if expected == 0:
        return trace_status_for(manifest), counts, False
    if failed == expected:
        return "failed", counts, False
    if inflight:
        return "", counts, True
    if capture in ("partial", "incomplete"):
        return "incomplete", counts, False
    if len(rows) < expected:
        return "incomplete", counts, False
    if failed or degraded:
        return "degraded", counts, False
    if ready == expected:
        if capture == "degraded":
            return "degraded", counts, False
        if capture == "failed":
            return "failed", counts, False
        return "ready", counts, False
    return "degraded", counts, False


def _aggregate(
    session: Session,
    *,
    attempt: EvalCaseAttempt,
    case_run: EvalCaseRun,
    expected: int,
    parser_versions: set[str],
) -> tuple[str, Counter[str]]:
    rows = _trace_rows(
        session, attempt_id=attempt.id, parser_versions=parser_versions
    )
    status, counts, inflight = _derive_trace_status(
        rows, manifest=_manifest(attempt), expected=expected
    )
    if inflight:
        return case_run.trace_status, counts
    return status, counts


def _insert_ingestion_event(
    session: Session,
    *,
    attempt: EvalCaseAttempt,
    case_run: EvalCaseRun,
    run: EvalRun,
    status: str,
    counts: Counter[str],
    expected: int,
    parser_versions: set[str],
    rows: list[TraceFile],
) -> bool:
    files = [
        {
            "logical_path": row.logical_path,
            "artifact_kind": row.artifact_kind,
            "status": row.status,
            "parser_version": row.parser_version,
            "error_code": row.error_code,
            "line_no": row.line_no,
            "error_summary": redact_text(row.error_summary, limit=240)
            if row.error_summary
            else None,
        }
        for row in rows[:MAX_EVENT_FILES]
    ]
    fingerprint_source = {
        "trace_status": status,
        "files": sorted(
            [
                [row.sha256, row.parser_version, row.status, row.error_code, row.line_no]
                for row in rows
            ]
        ),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_source, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:32]
    file_counts = {
        "expected": expected,
        "pending": counts["pending"],
        "parsing": counts["parsing"],
        "ready": counts["ready"],
        "degraded": counts["degraded"],
        "failed": counts["failed"],
    }
    errors = [item for item in files if item["error_summary"]][:MAX_EVENT_ERRORS]
    payload = {
        "fingerprint": fingerprint,
        "trace_status": status,
        "attempt_id": attempt.id,
        "file_counts": file_counts,
        "counts": file_counts,
        "parser_versions": sorted(parser_versions),
        "files": files,
        "errors": errors,
        "error_summary": errors[0]["error_summary"] if errors else None,
    }

    # Two workers can process different files of the same attempt concurrently.
    # Retrying with a recomputed sequence turns the unique
    # (case, attempt, sequence) constraint into an ordering lock instead of a
    # lost event. The fingerprint re-check drops the loser when both workers
    # observed the same final state.
    for _ in range(EVENT_INSERT_RETRIES):
        existing = list(
            session.scalars(
                select(EvalEvent).where(
                    EvalEvent.eval_case_run_id == case_run.id,
                    EvalEvent.attempt_no == attempt.attempt_no,
                    EvalEvent.event_type == EVENT_TYPE,
                )
            )
        )
        if any((event.payload_json or {}).get("fingerprint") == fingerprint for event in existing):
            return False
        sequence = int(
            session.scalar(
                select(func.max(EvalEvent.sequence)).where(
                    EvalEvent.eval_case_run_id == case_run.id,
                    EvalEvent.attempt_no == attempt.attempt_no,
                )
            )
            or 0
        ) + 1
        try:
            with session.begin_nested():
                session.add(
                    EvalEvent(
                        tenant_id=run.tenant_id,
                        eval_run_id=run.id,
                        eval_case_run_id=case_run.id,
                        attempt_no=attempt.attempt_no,
                        sequence=sequence,
                        event_type=EVENT_TYPE,
                        payload_json=payload,
                    )
                )
                session.flush()
            return True
        except IntegrityError:
            # The savepoint rolled back; loop and re-read the winner's sequence
            # and fingerprint before trying again.
            continue
    return False


def _emit_event(
    session: Session,
    *,
    attempt: EvalCaseAttempt,
    case_run: EvalCaseRun,
    run: EvalRun,
    status: str,
    counts: Counter[str],
    expected: int,
    parser_versions: set[str],
) -> bool:
    rows = _trace_rows(
        session, attempt_id=attempt.id, parser_versions=parser_versions
    )
    return _insert_ingestion_event(
        session,
        attempt=attempt,
        case_run=case_run,
        run=run,
        status=status,
        counts=counts,
        expected=expected,
        parser_versions=parser_versions,
        rows=rows,
    )


def _publish_trace_state(
    session: Session,
    *,
    attempt: EvalCaseAttempt,
    case_run: EvalCaseRun,
    run: EvalRun,
    expected: int,
    parser_versions: set[str],
) -> tuple[str, Counter[str], bool]:
    """Publish one attempt's status and event under a per-case write lock.

    The case row is locked before re-reading trace_file rows, so an older
    worker cannot allocate a sequence from a stale snapshot and publish a
    ``pending`` event after another worker already published ``ready``. While
    any file is still pending/parsing the publisher leaves the current DB
    status alone and emits nothing; the worker that finishes the last file
    publishes the single terminal event.
    """

    attempt_id = attempt.id
    case_run_id = case_run.id
    run_id = run.id
    # Start the locking transaction from committed state; callers have already
    # committed their per-file derived writes.
    session.commit()
    with session.begin():
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            # SQLite ignores FOR UPDATE but serializes writers. Touch the case
            # row now so two publishers cannot both read then write.
            session.execute(
                update(EvalCaseRun)
                .where(EvalCaseRun.id == case_run_id)
                .values(latest_attempt_no=EvalCaseRun.latest_attempt_no)
                .execution_options(synchronize_session=False)
            )
        locked_case = session.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.id == case_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        locked_attempt = session.scalars(
            select(EvalCaseAttempt)
            .where(EvalCaseAttempt.id == attempt_id)
            .execution_options(populate_existing=True)
        ).one()
        rows = _trace_rows(
            session, attempt_id=attempt_id, parser_versions=parser_versions
        )
        status, counts, inflight = _derive_trace_status(
            rows, manifest=_manifest(locked_attempt), expected=expected
        )
        if inflight:
            return locked_case.trace_status, counts, False

        set_case_trace_status(
            session,
            case_run=locked_case,
            attempt=locked_attempt,
            status=status,
        )
        emitted = _insert_ingestion_event(
            session,
            attempt=locked_attempt,
            case_run=locked_case,
            run=session.get(EvalRun, run_id),
            status=status,
            counts=counts,
            expected=expected,
            parser_versions=parser_versions,
            rows=rows,
        )
        return status, counts, emitted


def ingest_attempt(
    session: Session,
    attempt: EvalCaseAttempt,
    *,
    now: datetime | None = None,
    parser_registry: ParserRegistry | None = None,
    parser_version: str | None = None,
    store=None,
    settings: Settings | None = None,
    lease_seconds: float | None = None,
    worker_id: str | None = None,
    repair: bool = False,
) -> dict[str, Any]:
    """Parse every native file for one attempt with atomic upgrade semantics.

    Files are downloaded/verified/parsed before any derived row is written. A
    normal run then commits each file independently; a parser upgrade stages
    all files and replaces the attempt tree in one transaction. If any upgrade
    file fails, the old tree remains untouched and the new file rows are closed
    as failed/degraded provenance.
    """

    now = now or datetime.now(UTC)
    worker_id = worker_id or _new_worker_id()
    settings = settings or get_settings()
    lease_seconds = lease_seconds or settings.trace_ingestion_lease_seconds
    registry = parser_registry or DEFAULT_REGISTRY
    items = sorted(_trace_items(attempt), key=_item_key)
    case_run = session.get(EvalCaseRun, attempt.eval_case_run_id)
    if case_run is None:  # pragma: no cover
        raise LookupError(f"case run 不存在：{attempt.eval_case_run_id}")
    run = _run(session, attempt)
    if not items:
        status, _counts, _emitted = _publish_trace_state(
            session,
            attempt=attempt,
            case_run=case_run,
            run=run,
            expected=0,
            parser_versions=set(),
        )
        return {
            "files": 0,
            "claimed": 0,
            "skipped": 0,
            "ready": 0,
            "degraded": 0,
            "failed": 0,
            "trace_status": status,
        }

    manifest = _manifest(attempt)
    if store is None:
        store = object_store_from_settings(settings)

    rows: list[tuple[TraceFile, Any]] = []
    for ordinal, item in enumerate(items):
        detected, parser = _resolve_parser(
            registry,
            run=run,
            manifest=manifest,
            item=item,
            parser_version=parser_version,
        )
        row = _ensure_trace_file(
            session,
            attempt=attempt,
            case_run=case_run,
            run=run,
            manifest=manifest,
            item=item,
            detected=detected,
            parser=parser,
            ordinal=ordinal,
            now=now,
        )
        rows.append((row, parser))
    session.commit()

    parser_versions = {row.parser_version for row, _ in rows}
    existing_versions = set(
        session.scalars(
            select(TraceSession.parser_version)
            .where(TraceSession.eval_case_attempt_id == attempt.id)
            .distinct()
        )
    )
    # A replay is a single parser version already present in the tree. Any
    # other shape (new version, or a mixed-version batch) must stage the whole
    # attempt atomically instead of replacing files one at a time.
    single_version = next(iter(parser_versions)) if len(parser_versions) == 1 else None
    replacing = len(parser_versions) > 1 or (
        bool(existing_versions)
        and single_version is not None
        and single_version not in existing_versions
    )

    staged: list[_StagedTrace] = []
    for row, parser in rows:
        claimed = _claim(
            session,
            row.id,
            now=now,
            lease_seconds=lease_seconds,
            owner=worker_id,
            allow_reparse=replacing,
            allow_failed=repair,
        )
        row = session.get(TraceFile, row.id)
        if not claimed:
            continue
        staged.append(
            _load_and_parse(
                session,
                trace_file=row,
                parser=parser,
                store=store,
                now=now,
                lease_seconds=lease_seconds,
                lease_owner=worker_id,
            )
        )
    staged = [item for item in staged if not item.lease_lost]

    upgrade_aborted = False
    replaced = False
    # A partial claim means another worker still owns a file, so it is unsafe
    # to delete the previous tree. Leave the rows pending and aggregate later.
    if replacing and staged and len(staged) == len(rows):
        if any(item.parsed is None for item in staged):
            _abort_upgrade(session, staged_files=staged, now=now)
            session.commit()
            upgrade_aborted = True
        else:
            try:
                session.commit()
                with session.begin():
                    _replace_attempt_tree(session, attempt.id)
                    ordinals = _assign_session_ordinals(staged, start=0)
                    _persist_staged_tree(
                        session,
                        staged,
                        attempt_id=attempt.id,
                        now=now,
                        session_ordinals=ordinals,
                    )
                    _relink_parents(session, attempt.id)
                    superseded = list(
                        session.scalars(
                            select(TraceFile).where(
                                TraceFile.eval_case_attempt_id == attempt.id,
                                TraceFile.parser_version.not_in(sorted(parser_versions)),
                                TraceFile.status.in_(("pending", "parsing")),
                            )
                        )
                    )
                    for row in superseded:
                        row.status = "failed"
                        row.error_code = "superseded_by_parser"
                        row.error_summary = "已被新 parser 版本重放取代"
                        row.ended_at = now
                        row.lease_until = None
                        row.lease_owner = None
            except Exception:  # noqa: BLE001 - old tree must survive
                session.rollback()
                _abort_upgrade(session, staged_files=staged, now=now)
                session.commit()
                upgrade_aborted = True
            else:
                replaced = True

    if replacing and not replaced and not upgrade_aborted:
        # A peer still owns at least one upgrade file. Release this worker's
        # claims so the next sweep can retry the whole upgrade atomically.
        for item in staged:
            session.execute(
                update(TraceFile)
                .where(
                    TraceFile.id == item.trace_file_id,
                    TraceFile.status == "parsing",
                    TraceFile.lease_owner == worker_id[:LEASE_OWNER_LIMIT],
                )
                .values(status="pending", lease_until=None, lease_owner=None)
                .execution_options(synchronize_session=False)
            )
        session.commit()

    if not replacing:
        ordinals = _assign_session_ordinals(
            staged, start=_next_session_ordinal(session, attempt.id)
        )
        for item in staged:
            try:
                with session.begin_nested():
                    _replace_file_tree(session, item.trace_file_id)
                    _persist_staged_tree(
                        session,
                        [item],
                        attempt_id=attempt.id,
                        now=now,
                        session_ordinals=ordinals,
                    )
                session.commit()
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
                session.rollback()
                row = session.get(TraceFile, item.trace_file_id)
                if row is not None:
                    finished = _finish_lease(
                        session,
                        row.id,
                        owner=item.lease_owner,
                        status="failed",
                        error_code="storage_error",
                        error_summary=f"派生轨迹写入失败：{type(exc).__name__}",
                        line_no=item.line_no,
                        now=now,
                    )
                    if item.lease_owner is None and not finished:
                        _mark_failed(
                            row,
                            code="storage_error",
                            summary=f"派生轨迹写入失败：{type(exc).__name__}",
                            line_no=item.line_no,
                            now=now,
                        )
                    session.commit()
        _relink_parents(session, attempt.id)
        session.commit()

    case_run = session.get(EvalCaseRun, attempt.eval_case_run_id)
    status, counts, emitted = _publish_trace_state(
        session,
        attempt=attempt,
        case_run=case_run,
        run=run,
        expected=len(items),
        parser_versions=parser_versions,
    )

    claimed_ids = {item.trace_file_id for item in staged}
    outcomes = Counter(
        session.get(TraceFile, row.id).status
        for row, _ in rows
        if row.id in claimed_ids
    )
    return {
        "files": len(items),
        "claimed": len(staged),
        "skipped": len(rows) - len(staged),
        "ready": outcomes["ready"],
        "degraded": outcomes["degraded"],
        "failed": outcomes["failed"],
        "replaced": replaced,
        "upgrade_aborted": upgrade_aborted,
        "event": emitted,
        "trace_status": status,
    }


def ingest_trace_file(
    session: Session,
    trace_file: TraceFile,
    *,
    now: datetime | None = None,
    parser_registry: ParserRegistry | None = None,
    store=None,
    settings: Settings | None = None,
    lease_seconds: float | None = None,
    worker_id: str | None = None,
    repair: bool = False,
) -> dict[str, Any]:
    """Synchronous single-file entry (tests and one-off repair).

    A row whose parser version is not part of the attempt's current tree is a
    parser upgrade. Single-file replacement would leave a partial new-version
    tree if a peer file failed, so that case delegates to the atomic
    :func:`ingest_attempt` path. ``repair=True`` explicitly allows reclaiming a
    failed row for the same parser version; normal sweeps never do so.
    """

    now = now or datetime.now(UTC)
    worker_id = worker_id or _new_worker_id()
    settings = settings or get_settings()
    lease_seconds = lease_seconds or settings.trace_ingestion_lease_seconds
    attempt = session.get(EvalCaseAttempt, trace_file.eval_case_attempt_id)
    if attempt is None:  # pragma: no cover - FK guarantees the parent
        raise LookupError(f"attempt 不存在：{trace_file.eval_case_attempt_id}")
    case_run = session.get(EvalCaseRun, attempt.eval_case_run_id)
    run = _run(session, attempt)
    current_versions = set(
        session.scalars(
            select(TraceSession.parser_version)
            .where(TraceSession.eval_case_attempt_id == attempt.id)
            .distinct()
        )
    )
    if current_versions and trace_file.parser_version not in current_versions:
        return ingest_attempt(
            session,
            attempt,
            now=now,
            parser_registry=parser_registry,
            parser_version=trace_file.parser_version,
            store=store,
            settings=settings,
            lease_seconds=lease_seconds,
            worker_id=worker_id,
            repair=repair,
        )
    registry = parser_registry or DEFAULT_REGISTRY
    detected = trace_file.detected_format or detect_format(
        logical_path=trace_file.logical_path,
        content_type=None,
        native_format=trace_file.native_format,
    )
    try:
        parser = registry.resolve(
            runtime_key=trace_file.runtime_key,
            detected_format=detected,
            format_version=trace_file.format_version,
            parser_version=trace_file.parser_version,
        )
    except LookupError:
        parser = registry.default

    claimed = _claim(
        session,
        trace_file.id,
        now=now,
        lease_seconds=lease_seconds,
        owner=worker_id,
        allow_failed=repair,
    )
    trace_file = session.get(TraceFile, trace_file.id)
    if not claimed:
        return {"status": "skipped", "reason": "leased_or_terminal"}
    if store is None:
        store = object_store_from_settings(settings)
    staged = _load_and_parse(
        session,
        trace_file=trace_file,
        parser=parser,
        store=store,
        now=now,
        lease_seconds=lease_seconds,
        lease_owner=worker_id,
    )
    if staged.lease_lost:
        return {"status": "skipped", "reason": "lease_lost"}
    if staged.parsed is not None:
        try:
            with session.begin_nested():
                _replace_file_tree(session, staged.trace_file_id)
                ordinals = _assign_session_ordinals(
                    [staged], start=_next_session_ordinal(session, attempt.id)
                )
                _persist_staged_tree(
                    session,
                    [staged],
                    attempt_id=attempt.id,
                    now=now,
                    session_ordinals=ordinals,
                )
            session.commit()
        except Exception as exc:  # noqa: BLE001 - isolate this file
            session.rollback()
            row = session.get(TraceFile, staged.trace_file_id)
            if row is not None:
                finished = _finish_lease(
                    session,
                    row.id,
                    owner=staged.lease_owner,
                    status="failed",
                    error_code="storage_error",
                    error_summary=f"派生轨迹写入失败：{type(exc).__name__}",
                    line_no=staged.line_no,
                    now=now,
                )
                if staged.lease_owner is None and not finished:
                    _mark_failed(
                        row,
                        code="storage_error",
                        summary=f"派生轨迹写入失败：{type(exc).__name__}",
                        line_no=staged.line_no,
                        now=now,
                    )
                session.commit()
    _relink_parents(session, attempt.id)
    session.commit()

    parser_versions = {trace_file.parser_version}
    status, counts, _emitted = _publish_trace_state(
        session,
        attempt=attempt,
        case_run=case_run,
        run=run,
        expected=len(_trace_items(attempt)),
        parser_versions=parser_versions,
    )
    return {**{"status": staged.status}, "trace_status": status}


def backfill_missing(
    session: Session,
    *,
    limit: int = 10,
    now: datetime | None = None,
    parser_registry: ParserRegistry | None = None,
) -> dict[str, int]:
    """Enqueue completed attempts that predate M6-02 trace_file rows."""

    from .evaluations import TERMINAL_ATTEMPT_STATUSES

    now = now or datetime.now(UTC)
    has_rows = select(TraceFile.id).where(
        TraceFile.eval_case_attempt_id == EvalCaseAttempt.id
    ).exists()
    # Filter in SQL before applying the batch limit. Otherwise a long tail of
    # terminal attempts without trace manifests (score-only artifacts) can
    # permanently starve the attempts that do need a backfill.
    manifest = EvalCaseAttempt.artifact_manifest_json
    has_trace_payload = or_(
        manifest["trace_manifest"].is_not(None),
        manifest["objects"].is_not(None),
    )
    target = max(1, limit)
    chunk_size = 100
    cursor_created = None
    cursor_id = None
    enqueued = 0
    touched = 0
    exhausted = False
    while touched < target and not exhausted:
        stmt = (
            select(EvalCaseAttempt)
            .where(
                EvalCaseAttempt.status.in_(TERMINAL_ATTEMPT_STATUSES),
                ~has_rows,
                has_trace_payload,
            )
            .order_by(EvalCaseAttempt.created_at, EvalCaseAttempt.id)
            .limit(chunk_size)
        )
        if cursor_created is not None:
            stmt = stmt.where(
                or_(
                    EvalCaseAttempt.created_at > cursor_created,
                    (EvalCaseAttempt.created_at == cursor_created)
                    & (EvalCaseAttempt.id > cursor_id),
                )
            )
        chunk = list(session.scalars(stmt))
        if not chunk:
            break
        exhausted = len(chunk) < chunk_size
        for attempt in chunk:
            if touched >= target:
                break
            if not _trace_items(attempt):
                continue
            result = enqueue_attempt(
                session, attempt, now=now, parser_registry=parser_registry
            )
            if result["enqueued"]:
                enqueued += result["enqueued"]
                touched += 1
        cursor_created = chunk[-1].created_at
        cursor_id = chunk[-1].id
    session.commit()
    return {"attempts": touched, "files": enqueued}


def _tree_parser_versions(session: Session, attempt_id: str) -> set[str]:
    """Parser versions currently represented by the attempt's derived tree."""

    versions = set(
        session.scalars(
            select(TraceSession.parser_version)
            .where(TraceSession.eval_case_attempt_id == attempt_id)
            .distinct()
        )
    )
    if versions:
        return versions
    # Initial parses that failed before inserting a session still have
    # provenance rows; use those so their badge can settle as failed.
    return set(
        session.scalars(
            select(TraceFile.parser_version)
            .where(TraceFile.eval_case_attempt_id == attempt_id)
            .distinct()
        )
    )


def publish_stale_cases(
    session: Session,
    *,
    limit: int = 10,
) -> dict[str, int]:
    """Publish terminal trace trees whose case badge is still pending.

    A worker can commit the last ``trace_file(ready)`` and die before its
    publication transaction. ``ingest_pending`` then finds no claimable row and
    ``backfill_missing`` skips the attempt because provenance rows exist. This
    sweep re-derives the badge from terminal rows for the latest attempt only.
    """

    target = max(1, limit)
    # Only publish the crash-window state. ``incomplete`` is a terminal
    # manifest-derived badge and must never be re-derived by this sweep.
    terminal_file = exists().where(
        TraceFile.eval_case_attempt_id == EvalCaseAttempt.id,
        TraceFile.status.in_(("ready", "degraded", "failed")),
    )
    inflight_file = exists().where(
        TraceFile.eval_case_attempt_id == EvalCaseAttempt.id,
        TraceFile.status.in_(("pending", "parsing")),
    )
    cursor_created = None
    cursor_id = None
    exhausted = False
    scanned = published = events = 0
    # The SQL filter makes every returned row an actionable candidate, so the
    # chunk limit directly bounds a call. Keyset paging keeps repeated calls
    # deterministic and fair when a candidate is skipped mid-loop.
    while published < target and not exhausted:
        stmt = (
            select(EvalCaseRun, EvalCaseAttempt)
            .join(
                EvalCaseAttempt,
                (EvalCaseAttempt.eval_case_run_id == EvalCaseRun.id)
                & (EvalCaseAttempt.attempt_no == EvalCaseRun.latest_attempt_no),
            )
            .where(
                EvalCaseRun.trace_status == "pending",
                terminal_file,
                ~inflight_file,
            )
            .order_by(EvalCaseRun.created_at, EvalCaseRun.id)
            .limit(target)
        )
        if cursor_created is not None:
            stmt = stmt.where(
                or_(
                    EvalCaseRun.created_at > cursor_created,
                    (EvalCaseRun.created_at == cursor_created)
                    & (EvalCaseRun.id > cursor_id),
                )
            )
        chunk = list(session.execute(stmt))
        if not chunk:
            break
        exhausted = len(chunk) < target
        for case_run, attempt in chunk:
            if published >= target:
                break
            scanned += 1
            expected = len(_trace_items(attempt))
            if expected == 0:
                continue
            parser_versions = _tree_parser_versions(session, attempt.id)
            if not parser_versions:
                continue
            rows = _trace_rows(
                session, attempt_id=attempt.id, parser_versions=parser_versions
            )
            if not rows or any(
                row.status not in ("ready", "degraded", "failed") for row in rows
            ):
                continue
            before = case_run.trace_status
            status, _counts, emitted = _publish_trace_state(
                session,
                attempt=attempt,
                case_run=case_run,
                run=_run(session, attempt),
                expected=expected,
                parser_versions=parser_versions,
            )
            events += int(emitted)
            if emitted or status != before:
                published += 1
        cursor_created = chunk[-1][0].created_at
        cursor_id = chunk[-1][0].id
    return {"scanned": scanned, "published": published, "events": events}


def ingest_pending(
    session: Session,
    *,
    limit: int = 10,
    now: datetime | None = None,
    parser_registry: ParserRegistry | None = None,
    store=None,
    settings: Settings | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """Claim pending/stale trace files and ingest their attempts."""

    now = now or datetime.now(UTC)
    candidates = list(
        session.scalars(
            select(TraceFile)
            .where(
                TraceFile.status.in_(("pending", "parsing")),
                or_(
                    TraceFile.status == "pending",
                    TraceFile.lease_until.is_(None),
                    TraceFile.lease_until < now,
                ),
            )
            .order_by(TraceFile.created_at, TraceFile.id)
            .limit(max(1, limit))
        )
    )
    attempt_ids: list[str] = []
    for row in candidates:
        if row.eval_case_attempt_id not in attempt_ids:
            attempt_ids.append(row.eval_case_attempt_id)
    totals: dict[str, Any] = {
        "attempts": 0,
        "files": 0,
        "claimed": 0,
        "skipped": 0,
        "ready": 0,
        "degraded": 0,
        "failed": 0,
        "events": 0,
    }
    for attempt_id in attempt_ids:
        attempt = session.get(EvalCaseAttempt, attempt_id)
        if attempt is None:  # pragma: no cover - FK guarantees the parent
            continue
        result = ingest_attempt(
            session,
            attempt,
            now=now,
            parser_registry=parser_registry,
            store=store,
            settings=settings,
            worker_id=worker_id,
        )
        totals["attempts"] += 1
        for key in ("files", "claimed", "skipped", "ready", "degraded", "failed"):
            totals[key] += int(result.get(key) or 0)
        totals["events"] += int(bool(result.get("event")))
    return totals


def parser_version_for(runtime_key: str, detected_format: str) -> str:
    """Expose the default parser version without leaking registry internals."""

    return DEFAULT_REGISTRY.resolve(
        runtime_key=runtime_key, detected_format=detected_format
    ).parser_version


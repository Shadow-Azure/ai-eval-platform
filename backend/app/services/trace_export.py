"""Attempt-level raw trace export (M8-05).

One attempt's raw native trace objects (the main agent plus every subagent
session) are packed into a ZIP together with a ``manifest.json``. The archive
is built on disk and streamed back, so a large trace never has to be buffered
in memory. Missing objects are skipped and recorded in the manifest instead of
producing a broken archive.

The manifest is the first ZIP entry. It is written after staging the objects,
because the missing/size-mismatch flags have to be known before the entry is
created. ``stats.archive_size`` is the final archive size: the archive is
written with one entry set to ``ZIP_STORED`` (the manifest) so the size only
depends on the manifest's own decimal width, which converges in a few passes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EvalCaseRun, EvalRun, TraceFile, TraceSession
from . import trace_queries
from .evaluations import iso_utc
from .object_store import ObjectMissing, ObjectStore, ObjectStoreError
from .trace_redaction import redact_text

SCHEMA_VERSION = "trace-export/1"

_CHUNK_SIZE = 64 * 1024
_MAX_SEGMENT_LENGTH = 160
_MAX_MANIFEST_PASSES = 8
_ALLOWED_SEGMENT = re.compile(r"[^A-Za-z0-9._-]")
_NOTE = (
    "仅管理员可导出；本包为原始轨迹文件（不含归一化 span 数据），"
    "缺失对象已跳过并在 missing_files 中标记。"
)


class TraceExportError(Exception):
    """Maps a failed export onto an API error envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def safe_segment(value: str | None, *, fallback: str) -> str:
    """Reduce one archive path segment to its safe ASCII form.

    Only the basename survives: path separators, ``..`` runs, control
    characters and non-ASCII characters are removed. A missing or empty result
    falls back to a stable caller-provided id.
    """

    if not value:
        return fallback
    name = str(value).replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _ALLOWED_SEGMENT.sub("", name)
    while ".." in cleaned:
        cleaned = cleaned.replace("..", ".")
    cleaned = cleaned.lstrip(".").strip()
    if not cleaned:
        return fallback
    if len(cleaned) > _MAX_SEGMENT_LENGTH:
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and 0 < len(suffix) <= 16:
            keep = _MAX_SEGMENT_LENGTH - len(suffix) - 1
            cleaned = f"{stem[:keep]}.{suffix}"
        else:
            cleaned = cleaned[:_MAX_SEGMENT_LENGTH]
    return cleaned


def unique_archive_path(candidate: str, *, used: set[str], seed: str) -> str:
    """Register ``candidate``, disambiguating collisions with ``-<sha8>``."""

    if candidate not in used:
        used.add(candidate)
        return candidate
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    directory, _, name = candidate.rpartition("/")
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    for index in range(100):
        marker = f"-{digest}" if index == 0 else f"-{digest}-{index + 1}"
        filename = f"{stem}{marker}" + (f".{suffix}" if suffix else "")
        proposed = f"{directory}/{filename}" if directory else filename
        if proposed not in used:
            used.add(proposed)
            return proposed
    raise TraceExportError(500, "trace_export_failed", "导出路径冲突无法解决")


def content_disposition(filename: str) -> str:
    """``attachment`` header with an ASCII fallback plus RFC 5987 name."""

    fallback = "".join(
        character
        if character.isascii() and 32 <= ord(character) < 127 and character not in '"\\'
        else "_"
        for character in filename
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@dataclass
class ExportFile:
    """One ``TraceFile`` row and its resolved archive location."""

    trace_file: TraceFile
    archive_path: str
    session_ids: list[str]
    missing: bool = False
    size_mismatch: bool = False
    staged_path: Path | None = None
    actual_size: int = 0


@dataclass
class TraceExportPlan:
    """Validated attempt scope; the ZIP itself is built from this."""

    case_run_id: str
    case_key: str
    eval_run_id: str
    eval_run_name: str
    attempt_no: int
    attempt_id: str
    attempt: dict[str, Any]
    parser_versions: list[str]
    sessions: list[dict[str, Any]]
    stats: dict[str, Any]
    archive_root: str
    files: list[ExportFile]


@dataclass
class TraceExportArchive:
    """An on-disk ZIP ready to be streamed and then closed."""

    file: IO[bytes]
    filename: str
    size: int
    file_count: int
    archived_file_count: int
    missing_file_count: int
    attempt_no: int

    def iter_chunks(self, chunk_size: int = _CHUNK_SIZE) -> Iterator[bytes]:
        try:
            self.file.seek(0)
            while chunk := self.file.read(chunk_size):
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        if not self.file.closed:
            self.file.close()


def _case_and_run(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    case_run_id: str,
) -> tuple[EvalCaseRun, EvalRun]:
    row = session.execute(
        select(EvalCaseRun, EvalRun)
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
    ).first()
    if row is None:
        raise trace_queries.TraceQueryError(404, "eval_case_run_not_found", "评测用例不存在")
    return row[0], row[1]


def _session_directory(sessions: list[TraceSession], known_ids: set[str]) -> str:
    if not sessions:
        return "unlinked"
    # ``attempt_trace`` promotes a session with a dangling parent to the tree
    # root, so the archive must do the same (main/ rather than a subagent dir).
    if any(
        item.parent_session_id is None or item.parent_session_id not in known_ids
        for item in sessions
    ):
        return "main"
    child = sessions[0]
    agent = child.agent_name or child.agent_id
    return "subagents/" + safe_segment(agent, fallback=f"session-{child.id}")


def plan_trace_export(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    case_run_id: str,
    attempt_no: int | None = None,
) -> TraceExportPlan:
    """Resolve the scoped attempt, its files and the archive layout."""

    trace = trace_queries.attempt_trace(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        case_run_id=case_run_id,
        attempt_no=attempt_no,
    )
    attempt = trace.get("attempt")
    if not isinstance(attempt, dict):
        raise trace_queries.TraceQueryError(404, "attempt_not_found", "用例执行尝试不存在")
    attempt_id = str(attempt["id"])
    case, run = _case_and_run(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        case_run_id=case_run_id,
    )
    files = list(
        session.scalars(
            select(TraceFile)
            .where(
                TraceFile.eval_case_attempt_id == attempt_id,
                TraceFile.tenant_id == tenant_id,
            )
            .order_by(TraceFile.ordinal, TraceFile.id)
        )
    )
    if not files:
        raise TraceExportError(409, "trace_export_unavailable", "该执行尝试没有可导出的轨迹文件")
    trace_sessions = list(
        session.scalars(
            select(TraceSession)
            .where(
                TraceSession.eval_case_attempt_id == attempt_id,
                TraceSession.tenant_id == tenant_id,
            )
            .order_by(TraceSession.ordinal, TraceSession.id)
        )
    )
    by_file: dict[str, list[TraceSession]] = {}
    known_ids: set[str] = set()
    for item in trace_sessions:
        by_file.setdefault(item.trace_file_id, []).append(item)
        known_ids.add(item.id)

    number = int(attempt["attempt_no"])
    archive_root = (
        f"trace-{safe_segment(case.case_key, fallback=f'case-{case.id}')}"
        f"-attempt-{number}"
    )
    used: set[str] = set()
    export_files: list[ExportFile] = []
    for item in files:
        linked = by_file.get(item.id, [])
        directory = _session_directory(linked, known_ids)
        basename = safe_segment(item.logical_path, fallback=f"file-{item.id}")
        archive_path = unique_archive_path(
            f"{directory}/{basename}", used=used, seed=item.id
        )
        export_files.append(
            ExportFile(
                trace_file=item,
                archive_path=archive_path,
                session_ids=[entry.id for entry in linked],
            )
        )
    return TraceExportPlan(
        case_run_id=case.id,
        case_key=case.case_key,
        eval_run_id=run.id,
        eval_run_name=run.name,
        attempt_no=number,
        attempt_id=attempt_id,
        attempt=attempt,
        parser_versions=list(trace.get("parser_versions") or []),
        sessions=list(trace.get("sessions") or []),
        stats=dict(trace.get("stats") or {}),
        archive_root=archive_root,
        files=export_files,
    )


def _stage_files(plan: TraceExportPlan, store: ObjectStore, staging_dir: Path) -> None:
    for index, item in enumerate(plan.files):
        try:
            reported_size = int(store.head_object(item.trace_file.object_key))
        except ObjectMissing:
            item.missing = True
            continue
        except ObjectStoreError as exc:
            raise TraceExportError(
                502, "trace_export_failed", "对象存储读取失败"
            ) from exc
        item.size_mismatch = reported_size != int(item.trace_file.size)
        staged = staging_dir / f"{index:04d}.bin"
        try:
            with staged.open("wb") as sink:
                for chunk in store.iter_object(item.trace_file.object_key):
                    sink.write(chunk)
        except ObjectMissing:
            staged.unlink(missing_ok=True)
            item.missing = True
            item.size_mismatch = False
            continue
        except ObjectStoreError as exc:
            raise TraceExportError(
                502, "trace_export_failed", "对象存储读取失败"
            ) from exc
        item.actual_size = staged.stat().st_size
        if item.actual_size != reported_size:
            item.size_mismatch = True
        item.staged_path = staged


def _manifest(
    plan: TraceExportPlan,
    *,
    exported_at: datetime,
    archive_size: int,
) -> dict[str, Any]:
    archived = [item for item in plan.files if not item.missing]
    missing = [item for item in plan.files if item.missing]
    attempts = plan.attempt
    total_size = sum(item.actual_size for item in archived)
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": iso_utc(exported_at),
        "case": {
            "id": plan.case_run_id,
            "key": redact_text(plan.case_key, limit=200),
        },
        "eval_run": {
            "id": plan.eval_run_id,
            "name": redact_text(plan.eval_run_name, limit=200),
        },
        "attempt": {
            "id": plan.attempt_id,
            "no": plan.attempt_no,
            "status": attempts.get("status"),
            "phase": attempts.get("phase"),
            "started_at": attempts.get("started_at"),
            "ended_at": attempts.get("ended_at"),
        },
        "parser_versions": plan.parser_versions,
        "stats": {
            "session_count": int(plan.stats.get("session_count", 0)),
            "span_count": int(plan.stats.get("span_count", 0)),
            "file_count": len(plan.files),
            "archived_file_count": len(archived),
            "missing_file_count": len(missing),
            "total_size": total_size,
            "archive_size": archive_size,
        },
        "sessions": plan.sessions,
        "files": [
            {
                "id": item.trace_file.id,
                "logical_path": (
                    redact_text(item.trace_file.logical_path, limit=512)
                    if item.trace_file.logical_path
                    else None
                ),
                "archive_path": item.archive_path,
                "artifact_kind": item.trace_file.artifact_kind,
                "parser_version": item.trace_file.parser_version,
                "status": item.trace_file.status,
                "sha256": item.trace_file.sha256,
                "size": int(item.trace_file.size),
                "session_ids": item.session_ids,
                "missing": item.missing,
                "size_mismatch": item.size_mismatch,
            }
            for item in plan.files
        ],
        "missing_files": [
            {
                "id": item.trace_file.id,
                "logical_path": (
                    redact_text(item.trace_file.logical_path, limit=512)
                    if item.trace_file.logical_path
                    else None
                ),
                "archive_path": item.archive_path,
                "artifact_kind": item.trace_file.artifact_kind,
                "sha256": item.trace_file.sha256,
                "size": int(item.trace_file.size),
                "reason": "object_missing",
            }
            for item in missing
        ],
        "note": _NOTE,
    }


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _write_zip(
    target: IO[bytes],
    *,
    plan: TraceExportPlan,
    manifest_bytes: bytes,
    exported_at: datetime,
) -> int:
    date_time = exported_at.timetuple()[:6]
    target.seek(0)
    target.truncate(0)
    with zipfile.ZipFile(target, mode="w", allowZip64=True) as archive:
        manifest_info = zipfile.ZipInfo(
            f"{plan.archive_root}/manifest.json", date_time=date_time
        )
        # The manifest stays uncompressed so its entry length only depends on
        # its own bytes; that keeps ``archive_size`` solvable without an
        # unbounded rebuild loop.
        manifest_info.compress_type = zipfile.ZIP_STORED
        manifest_info.external_attr = 0o600 << 16
        archive.writestr(manifest_info, manifest_bytes)
        for item in plan.files:
            if item.missing or item.staged_path is None:
                continue
            info = zipfile.ZipInfo(
                f"{plan.archive_root}/{item.archive_path}", date_time=date_time
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            with (
                archive.open(info, mode="w") as sink,
                item.staged_path.open("rb") as source,
            ):
                shutil.copyfileobj(source, sink, _CHUNK_SIZE)
    return target.tell()


def _build_archive_file(
    plan: TraceExportPlan, staging_dir: Path, exported_at: datetime
) -> tuple[IO[bytes], int]:
    archive_file = tempfile.TemporaryFile()
    try:
        candidate = 0
        for _ in range(_MAX_MANIFEST_PASSES):
            manifest = _manifest(plan, exported_at=exported_at, archive_size=candidate)
            written = _write_zip(
                archive_file,
                plan=plan,
                manifest_bytes=_manifest_bytes(manifest),
                exported_at=exported_at,
            )
            if written == candidate:
                break
            candidate = written
        else:
            # Pathological non-convergence (never observed): keep the closest
            # value rather than failing an otherwise valid export.
            manifest = _manifest(plan, exported_at=exported_at, archive_size=candidate)
            _write_zip(
                archive_file,
                plan=plan,
                manifest_bytes=_manifest_bytes(manifest),
                exported_at=exported_at,
            )
        return archive_file, archive_file.tell()
    except BaseException:
        archive_file.close()
        raise


def build_trace_export(plan: TraceExportPlan, store: ObjectStore) -> TraceExportArchive:
    """Stage every object, then write and return the streamable ZIP."""

    staging_dir = Path(tempfile.mkdtemp(prefix="trace-export-"))
    try:
        _stage_files(plan, store, staging_dir)
        exported_at = datetime.now(UTC)
        archive_file, size = _build_archive_file(plan, staging_dir, exported_at)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    archived = [item for item in plan.files if not item.missing]
    return TraceExportArchive(
        file=archive_file,
        filename=f"{plan.archive_root}.zip",
        size=size,
        file_count=len(plan.files),
        archived_file_count=len(archived),
        missing_file_count=len(plan.files) - len(archived),
        attempt_no=plan.attempt_no,
    )


__all__ = [
    "SCHEMA_VERSION",
    "ExportFile",
    "TraceExportArchive",
    "TraceExportError",
    "TraceExportPlan",
    "build_trace_export",
    "content_disposition",
    "plan_trace_export",
    "safe_segment",
    "unique_archive_path",
]

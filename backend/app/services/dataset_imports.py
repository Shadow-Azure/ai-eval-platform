"""Uploaded dataset import lifecycle (M5-15).

The console uploads a JSON/JSONL file, validates it in an isolated worker,
shows a validation report and only then lets the operator materialise an
immutable version. This module owns:

- the persistent report shape (counts, field completeness, bounded errors);
- validation / materialisation of one uploaded import;
- temporary-file lifecycle (created by the API, consumed by the worker);
- the stale-import reaper used by the background worker loop.

Parsing itself lives in :mod:`app.datasets.source`; version writing is shared
with the legacy JSON endpoint through ``materialize_dataset_version``.
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:  # POSIX only; the limits are optional on other platforms.
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..datasets import CaseIssue, DatasetAdapter, get_adapter
from ..datasets.source import SourceFormatError, iter_source_records, source_mode
from ..models import DatasetImport
from .dataset_images import DatasetImageUnavailable, effective_sources, image_for_case, probe_case_images
from .datasets import (
    DatasetSourceChanged,
    DatasetVersionConflict,
    _effective_digests,
    materialize_dataset_version,
)

ACTIVE_IMPORT_STATUSES = ("queued", "validating", "committing")
MAX_ERRORS_SAFE = 200


class DatasetImportNotFound(LookupError):
    pass


class DatasetImportStateConflict(Exception):
    """The import is not in a state that allows the requested transition."""


@dataclass
class ValidationResult:
    report: dict[str, Any]
    errors: list[CaseIssue] = field(default_factory=list)
    images: dict[str, str] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        # A fatal layout error (unterminated array, unreadable source) has no
        # per-record error to count but must still block the import.
        return (
            not self.report.get("fatal")
            and self.report["invalid"] == 0
            and self.report["total"] > 0
        )


def import_root(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).dataset_import_dir)


def source_path(record: DatasetImport, settings: Settings | None = None) -> Path:
    """Resolve the staged file, rejecting keys that escape the import root."""

    if not record.storage_key:
        raise DatasetSourceChanged("导入记录缺少源文件")
    if Path(record.storage_key).name != record.storage_key:
        raise DatasetSourceChanged("导入源文件路径非法")
    return import_root(settings) / record.storage_key


def delete_source_file(record: DatasetImport, settings: Settings | None = None) -> None:
    try:
        source_path(record, settings).unlink(missing_ok=True)
    except (DatasetSourceChanged, OSError):
        return


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone=True columns."""

    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _issue_dict(issue: CaseIssue) -> dict[str, Any]:
    return issue.as_dict()


def _completeness(fields: tuple[str, ...], present: dict[str, int], total: int) -> dict[str, dict[str, Any]]:
    if not fields:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for field_name in fields:
        count = present.get(field_name, 0)
        percent = round(count * 100.0 / total, 1) if total else 0.0
        result[field_name] = {"present": count, "percent": percent}
    return result


def validate_source_file(
    path: Path,
    adapter: DatasetAdapter,
    *,
    error_limit: int = MAX_ERRORS_SAFE,
    progress: Callable[[int], None] | None = None,
    settings: Settings | None = None,
) -> ValidationResult:
    """Stream the file and build the persisted validation report."""

    settings = settings or get_settings()
    image_arch = getattr(settings, "dataset_image_arch", "x86_64")
    case_images: dict[str, str] = {}
    fields = tuple(getattr(adapter, "required_fields", ()) or ())
    present = {field_name: 0 for field_name in fields}
    errors: list[CaseIssue] = []
    error_codes: dict[str, int] = {}
    error_fields: dict[str, int] = {}
    seen_keys: dict[str, int] = {}
    total = valid = invalid = 0
    fatal: tuple[str, str] | None = None

    def append(error: CaseIssue) -> None:
        error_codes[error.code] = error_codes.get(error.code, 0) + 1
        if error.field:
            error_fields[error.field] = error_fields.get(error.field, 0) + 1
        if len(errors) < error_limit:
            errors.append(error)

    try:
        mode = source_mode(path)
    except SourceFormatError as exc:
        return ValidationResult(
            report={
                "mode": "unknown",
                "total": 0,
                "valid": 0,
                "invalid": 0,
                "field_completeness": {},
                "error_codes": {exc.code: 1},
                "error_fields": {},
                "errors": [
                    {
                        "code": exc.code,
                        "field": None,
                        "message": exc.message,
                        "case_index": None,
                        "case_key": None,
                    }
                ],
                "errors_truncated": 0,
                "warnings": [],
                "fatal": True,
                "generated_at": _now().isoformat(timespec="seconds"),
            },
            errors=[CaseIssue(code=exc.code, message=exc.message)],
        )

    iterator = iter_source_records(path)
    while True:
        try:
            record = next(iterator)
        except StopIteration:
            break
        except SourceFormatError as exc:
            fatal = (exc.code, exc.message)
            append(CaseIssue(code=exc.code, message=exc.message))
            break

        total += 1
        if record.error:
            invalid += 1
            append(CaseIssue(code="invalid_json", message=record.error, case_index=record.index))
            if progress and total % progress_step() == 0:
                progress(total)
            continue

        raw = record.value or {}
        for field_name in fields:
            value = raw.get(field_name)
            if isinstance(value, str) and value.strip():
                present[field_name] = present.get(field_name, 0) + 1

        issues = list(adapter.validate_case(raw))
        case_key: str | None = None
        normalized = None
        try:
            normalized = adapter.normalize_case(raw)
            case_key = normalized.case_key
        except (KeyError, TypeError, ValueError):
            case_key = None
        if not issues and case_key is not None and normalized is not None:
            image = image_for_case(str(getattr(adapter, "key", "")), str(case_key), raw, arch=image_arch)
            if image:
                case_images[str(case_key)] = image
        if case_key is not None:
            if case_key in seen_keys:
                issues.append(
                    CaseIssue(
                        code="duplicate_case_key",
                        message=(
                            f"case_key 重复：{str(case_key)[:120]}"
                            f"（第 {seen_keys[case_key]} 条与第 {record.index} 条）"
                        ),
                        case_index=record.index,
                        case_key=case_key,
                    )
                )
            else:
                seen_keys[case_key] = record.index
        for issue in issues:
            issue.case_index = record.index
            # The source is user-controlled; report keys are bounded so one
            # pathological record cannot inflate the persisted report.
            issue.case_key = str(case_key)[:200] if case_key is not None else None
            append(issue)
        if issues:
            invalid += 1
        else:
            valid += 1
        if progress and total % progress_step() == 0:
            progress(total)

    if fatal is None and total == 0:
        fatal = ("empty_source", "数据文件不包含任何记录")
        append(CaseIssue(code="empty_source", message=fatal[1]))
    report = {
        "mode": mode,
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "field_completeness": _completeness(fields, present, total),
        "error_codes": error_codes,
        "error_fields": error_fields,
        "errors": [_issue_dict(issue) for issue in errors],
        "errors_truncated": max(0, sum(error_codes.values()) - len(errors)),
        "warnings": [],
        "fatal": fatal is not None,
        "fatal_code": fatal[0] if fatal else None,
        "fatal_message": fatal[1] if fatal else None,
        "generated_at": _now().isoformat(timespec="seconds"),
    }
    return ValidationResult(report=report, errors=errors, images=case_images)


def progress_step() -> int:
    return max(100, int(get_settings().dataset_import_progress_step))


def _touch(record: DatasetImport, *, processed: int | None = None) -> None:
    record.status_updated_at = _now()
    if processed is not None:
        record.processed_count = processed


def _fail_record(record: DatasetImport, *, code: str, message: str, details: dict | None = None) -> None:
    record.status = "failed"
    _touch(record)
    report = dict(record.validation_report_json or {})
    report.setdefault("errors", [])
    report["fatal"] = True
    report["fatal_code"] = code
    report["fatal_message"] = message
    if details:
        report["fatal_details"] = details
    report.setdefault("generated_at", _now().isoformat(timespec="seconds"))
    record.validation_report_json = report
    record.error_report_json = {
        "adapter_key": record.adapter_key,
        "dataset_slug": record.dataset_slug,
        "version": record.version,
        "error_count": 1,
        "codes": [code],
        "errors": [
            {
                "code": code,
                "field": None,
                "message": message,
                "case_index": None,
                "case_key": None,
            }
        ],
    }


def mark_worker_failure(
    session: Session,
    import_id: str,
    *,
    code: str,
    message: str,
    details: dict | None = None,
) -> bool:
    """Mark a still-active import failed; never overwrite a terminal row."""

    record = session.get(DatasetImport, import_id)
    if record is None or record.status not in ACTIVE_IMPORT_STATUSES:
        return False
    _fail_record(record, code=code, message=message, details=details)
    session.commit()
    delete_source_file(record)
    return True


def expire_validated_import(session: Session, import_id: str) -> bool:
    """Fail a validated import that was never committed (release its file)."""

    record = session.get(DatasetImport, import_id)
    if record is None or record.status != "validated":
        return False
    _fail_record(
        record,
        code="import_expired",
        message="导入超过保留时间仍未生成版本，请重新上传",
    )
    session.commit()
    delete_source_file(record)
    return True


def begin_validation(session: Session, record: DatasetImport) -> bool:
    """Atomically claim a queued import for validation.

    Returns False when another actor (usually a cancel) already moved the row
    out of ``queued``; the worker then exits without resurrecting it.
    """

    from sqlalchemy import update

    result = session.execute(
        update(DatasetImport)
        .where(DatasetImport.id == record.id, DatasetImport.status == "queued")
        .values(status="validating", status_updated_at=_now(), processed_count=0)
    )
    session.commit()
    if result.rowcount != 1:
        return False
    session.refresh(record)
    return True


def validate_uploaded_import(
    session: Session,
    record: DatasetImport,
    *,
    settings: Settings | None = None,
) -> ValidationResult:
    """Validate one uploaded import inside the worker process."""

    settings = settings or get_settings()
    if not begin_validation(session, record):
        return ValidationResult(
            report={
                "mode": "unknown",
                "total": 0,
                "valid": 0,
                "invalid": 0,
                "field_completeness": {},
                "error_codes": {},
                "error_fields": {},
                "errors": [],
                "errors_truncated": 0,
                "warnings": [],
                "fatal": False,
                "generated_at": _now().isoformat(timespec="seconds"),
            }
        )
    path = source_path(record, settings)

    def on_progress(processed: int) -> None:
        record.processed_count = processed
        _touch(record)
        session.commit()

    try:
        adapter = get_adapter(record.adapter_key)
    except Exception as exc:  # AdapterNotFound is a LookupError
        report = {
            "mode": "unknown",
            "total": 0,
            "valid": 0,
            "invalid": 0,
            "field_completeness": {},
            "error_codes": {"adapter_not_found": 1},
            "error_fields": {},
            "errors": [
                {
                    "code": "adapter_not_found",
                    "field": "adapter_key",
                    "message": f"未注册的数据集适配器：{record.adapter_key}",
                    "case_index": None,
                    "case_key": None,
                }
            ],
            "errors_truncated": 0,
            "warnings": [],
            "fatal": True,
            "fatal_code": "adapter_not_found",
            "fatal_message": f"未注册的数据集适配器：{record.adapter_key}",
            "generated_at": _now().isoformat(timespec="seconds"),
        }
        result = ValidationResult(
            report=report,
            errors=[CaseIssue(code="adapter_not_found", message=str(exc), field="adapter_key")],
        )
    else:
        try:
            result = validate_source_file(
                path,
                adapter,
                error_limit=max(1, int(settings.dataset_import_error_limit)),
                progress=on_progress,
                settings=settings,
            )
        except OSError:
            result = ValidationResult(
                report={
                    "mode": "unknown",
                    "total": record.processed_count,
                    "valid": 0,
                    "invalid": 0,
                    "field_completeness": {},
                    "error_codes": {"source_file_unreadable": 1},
                    "error_fields": {},
                    "errors": [
                        {
                            "code": "source_file_unreadable",
                            "field": None,
                            "message": "导入源文件无法读取，请重新上传",
                            "case_index": None,
                            "case_key": None,
                        }
                    ],
                    "errors_truncated": 0,
                    "warnings": [],
                    "fatal": True,
                    "fatal_code": "source_file_unreadable",
                    "fatal_message": "导入源文件无法读取，请重新上传",
                    "generated_at": _now().isoformat(timespec="seconds"),
                },
                errors=[CaseIssue(code="source_file_unreadable", message="导入源文件无法读取")],
            )

    session.refresh(record)
    if record.status not in ("validating", "validated"):
        delete_source_file(record, settings)
        return result

    report = result.report
    if result.valid and result.images:
        try:
            report["image_check"] = probe_case_images(
                result.images,
                settings=settings,
                sources=effective_sources(session, settings),
            )
        except Exception as exc:  # probe failures must block, never crash the worker
            report["image_check"] = {
                "status": "blocked",
                "reason": f"镜像探测失败：{type(exc).__name__}",
                "sources": [],
                "checked": len(result.images),
                "available": 0,
                "cases": [],
                "unavailable": [],
                "cases_truncated": 0,
            }
    else:
        report["image_check"] = {
            "status": "skipped",
            "reason": "没有可探测的用例镜像" if result.valid else "字段校验未通过，跳过镜像探测",
            "sources": [],
            "checked": 0,
            "available": 0,
            "cases": [],
            "unavailable": [],
            "cases_truncated": 0,
        }
    record.total_count = int(report["total"])
    record.valid_count = int(report["valid"])
    record.invalid_count = int(report["invalid"])
    record.processed_count = int(report["total"])
    record.validation_report_json = report
    if report.get("errors"):
        record.error_report_json = {
            "adapter_key": record.adapter_key,
            "dataset_slug": record.dataset_slug,
            "version": record.version,
            "error_count": sum(report.get("error_codes", {}).values()),
            "codes": sorted(report.get("error_codes", {})),
            "errors": report["errors"],
        }
    else:
        record.error_report_json = {}
    record.status = "validated" if result.valid else "failed"
    _touch(record)
    session.commit()
    if record.status == "failed":
        delete_source_file(record, settings)
    return result


def _stream_records(path: Path) -> Iterator[dict[str, Any]]:
    for record in iter_source_records(path):
        if record.error or record.value is None:
            raise DatasetSourceChanged(record.error or "导入源文件包含无法解析的记录")
        yield record.value


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def materialize_uploaded_import(
    session: Session,
    record: DatasetImport,
    *,
    settings: Settings | None = None,
) -> None:
    """Build the immutable version from the staged file (commit phase)."""

    settings = settings or get_settings()
    path = source_path(record, settings)
    if not path.is_file():
        raise DatasetSourceChanged("导入源文件已丢失，请重新上传")

    expected_digest = record.file_digest
    if expected_digest and _file_digest(path) != expected_digest:
        raise DatasetSourceChanged("导入源文件已改变，拒绝用新内容生成版本")

    execution_digest, scoring_digest = _effective_digests(
        record.execution_image_digest, record.scoring_image_digest
    )

    record.status = "committing"
    record.processed_count = 0
    _touch(record)
    session.commit()

    def records() -> Iterator[dict[str, Any]]:
        # Never commit part-way through the version transaction: a later parse
        # failure must roll back every case instead of leaving a partial
        # version. The processed counter is updated in memory and persisted
        # with the final status.
        yield from _stream_records(path)

    try:
        materialize_dataset_version(
            session,
            dataset_slug=record.dataset_slug,
            dataset_name=record.dataset_name or record.dataset_slug,
            adapter_key=record.adapter_key,
            version=record.version,
            source_uri=record.source_uri,
            source_revision=record.source_revision or "",
            records=records(),
            total_count=record.total_count,
            environment_image=None,
            execution_image_digest=record.execution_image_digest,
            scoring_image_digest=record.scoring_image_digest,
            created_by=record.created_by,
            import_record=record,
            source_digest=record.file_digest,
        )
    except DatasetVersionConflict:
        session.rollback()
        delete_source_file(record, settings)
        return
    except Exception:
        session.rollback()
        raise
    delete_source_file(record, settings)


def mark_commit_failed(
    session: Session,
    import_id: str,
    *,
    message: str,
    code: str = "materialize_failed",
) -> bool:
    session.rollback()
    return mark_worker_failure(session, import_id, code=code, message=message)


def sweep_orphan_files(
    session: Session,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Delete staged files with no import row (crash between write and insert)."""

    settings = settings or get_settings()
    now = now or _now()
    root = import_root(settings)
    if not root.is_dir():
        return 0
    referenced = {
        key
        for key in session.scalars(
            select(DatasetImport.storage_key).where(DatasetImport.storage_key.is_not(None))
        )
        if key
    }
    grace = timedelta(seconds=max(60, int(settings.dataset_import_stale_seconds)))
    removed = 0
    for path in root.iterdir():
        if not path.is_file() or path.name in referenced:
            continue
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if now - modified < grace:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def reap_stale_imports(
    session: Session,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Fail imports whose worker died, so the wizard cannot spin forever."""

    settings = settings or get_settings()
    now = now or _now()
    cutoff = now - timedelta(seconds=max(60, int(settings.dataset_import_stale_seconds)))
    # A validated import that nobody commits also owns a staged file, so it
    # expires on the same heartbeat window instead of leaking disk forever.
    rows = list(
        session.scalars(
            select(DatasetImport).where(
                DatasetImport.status.in_((*ACTIVE_IMPORT_STATUSES, "validated"))
            )
        )
    )
    reaped = 0
    for record in rows:
        updated = _as_utc(record.status_updated_at or record.created_at)
        if updated is None or updated > cutoff:
            continue
        if record.status == "validated":
            expire_validated_import(session, record.id)
        else:
            mark_worker_failure(
                session,
                record.id,
                code="worker_interrupted",
                message="导入 worker 超时或中断，请重新上传",
            )
        reaped += 1
    sweep_orphan_files(session, settings=settings, now=now)
    return reaped


# ---------------------------------------------------------------------------
# Isolated subprocess runner
# ---------------------------------------------------------------------------


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _worker_env(settings: Settings, database_url: str, backend: Path) -> dict[str, str]:
    """Minimal child environment: no runner, model or session credentials.

    The parser only needs its database connection and import limits; passing
    the API process's full environment would hand every secret to a process
    that parses untrusted data.
    """

    keep = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "PYTHONIOENCODING",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "ENVIRONMENT",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if key in keep or key.startswith(("DATASET_IMPORT_", "PG", "LC_"))
    }
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(backend) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    # pydantic-settings may have loaded these from a .env file, which is not
    # visible in os.environ; pass the resolved values so the child uses the
    # same directory, limits and timeouts as the API process.
    for key in (
        "dataset_import_dir",
        "dataset_import_max_bytes",
        "dataset_import_timeout_seconds",
        "dataset_import_memory_limit_mb",
        "dataset_import_stale_seconds",
        "dataset_import_error_limit",
        "dataset_import_progress_step",
        "dataset_image_registries",
        "dataset_image_arch",
        "dataset_image_probe_timeout_seconds",
        "dataset_image_probe_concurrency",
        "dataset_image_check_enabled",
    ):
        env[key.upper()] = str(getattr(settings, key))
    return env


def _apply_limits(memory_limit_mb: int, cpu_seconds: int) -> None:
    if os.name != "posix" or resource is None:
        return
    memory = max(64, memory_limit_mb) * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 30))
    except (ValueError, OSError):
        pass


def _session_factory(database_url: str):
    from sqlalchemy import create_engine

    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def run_import_job(
    import_id: str,
    stage: str,
    *,
    database_url: str,
    timeout_seconds: float | None = None,
) -> None:
    """Run one import phase in an isolated process (called from a background task)."""

    settings = get_settings()
    timeout = float(timeout_seconds or settings.dataset_import_timeout_seconds)
    command = [sys.executable, "-m", "app.worker.dataset_import", import_id, stage]
    backend_path = _backend_root()
    env = _worker_env(settings, database_url, backend_path)
    # Limits are applied by the child itself: preexec_fn is not safe in a
    # threaded API process, and the worker only needs its own address space.
    popen_kwargs: dict[str, Any] = {
        "cwd": str(backend_path),
        "env": env,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)
    timed_out = False
    try:
        _stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
        else:  # pragma: no cover - Windows fallback
            process.kill()
        _stdout, stderr = process.communicate()
    except Exception:
        process.kill()
        raise

    engine, session_factory = _session_factory(database_url)
    try:
        with session_factory() as session:
            if timed_out:
                mark_worker_failure(
                    session,
                    import_id,
                    code="worker_timeout",
                    message=f"导入 worker 超过 {int(timeout)} 秒未完成，已终止",
                )
            elif process.returncode != 0:
                code = "worker_failed"
                message = "导入 worker 异常退出，请重新上传"
                if stderr:
                    print(
                        f"dataset import worker {import_id} exited {process.returncode}: "
                        f"{stderr.strip()[:400]}",
                        file=sys.stderr,
                        flush=True,
                    )
                mark_worker_failure(session, import_id, code=code, message=message)
    finally:
        engine.dispose()


CANCELLABLE_IMPORT_STATUSES = ("queued", "validating", "validated")


def cancel_import(session: Session, import_id: str) -> DatasetImport:
    """Atomically cancel an import that has not started materialising.

    A conditional UPDATE closes the check-then-act window with a concurrent
    worker claim: a worker that already moved the row to validating still
    re-reads the status before persisting and exits when it sees cancelled.
    """

    from sqlalchemy import update

    record = session.get(DatasetImport, import_id)
    if record is None:
        raise DatasetImportNotFound(import_id)
    now = _now()
    result = session.execute(
        update(DatasetImport)
        .where(
            DatasetImport.id == import_id,
            DatasetImport.status.in_(CANCELLABLE_IMPORT_STATUSES),
        )
        .values(status="cancelled", status_updated_at=now)
    )
    session.commit()
    if result.rowcount != 1:
        raise DatasetImportStateConflict(import_id)
    session.refresh(record)
    delete_source_file(record)
    return record


def begin_commit(session: Session, import_id: str) -> DatasetImport:
    """Atomically move a validated import into the committing phase."""

    from sqlalchemy import update

    record = session.get(DatasetImport, import_id)
    if record is None:
        raise DatasetImportNotFound(import_id)
    if record.status != "validated":
        raise DatasetImportStateConflict(import_id)
    image_check = (record.validation_report_json or {}).get("image_check") or {}
    if image_check.get("status") == "blocked":
        raise DatasetImageUnavailable(image_check)
    now = _now()
    result = session.execute(
        update(DatasetImport)
        .where(DatasetImport.id == import_id, DatasetImport.status == "validated")
        .values(status="committing", status_updated_at=now, processed_count=0, error_report_json={})
    )
    session.commit()
    if result.rowcount != 1:
        raise DatasetImportStateConflict(import_id)
    # Keep the response accurate without re-opening a read transaction: the
    # isolated worker starts immediately after this response.
    record.status = "committing"
    record.status_updated_at = now
    record.processed_count = 0
    record.error_report_json = {}
    return record


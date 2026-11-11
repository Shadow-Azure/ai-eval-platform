"""One dataset-import phase in an isolated process (M5-15).

Usage: ``python -m app.worker.dataset_import <import_id> validate|materialize``

The API and the background worker start this module with a wall-clock timeout,
an address-space limit and its own database connection, so a malformed or
hostile 1 GB source cannot exhaust the control-plane process. Business
failures are persisted on the import row; the exit code stays 0 so callers do
not mistake a reported validation failure for an infrastructure crash.
"""

from __future__ import annotations

import sys

from ..config import get_settings
from ..db import SessionLocal
from ..models import DatasetImport
from ..services.dataset_imports import (
    DatasetImportNotFound,
    DatasetSourceChanged,
    _apply_limits,
    mark_worker_failure,
    materialize_uploaded_import,
    validate_uploaded_import,
)

VALIDATE = "validate"
MATERIALIZE = "materialize"


def _run(import_id: str, stage: str) -> int:
    settings = get_settings()
    # Independent process limits (POSIX): address space and CPU. The parent
    # additionally enforces the wall-clock timeout by killing the process.
    _apply_limits(
        settings.dataset_import_memory_limit_mb,
        settings.dataset_import_timeout_seconds + 30,
    )
    with SessionLocal() as session:
        record = session.get(DatasetImport, import_id)
        if record is None:
            print(f"dataset import {import_id} not found", file=sys.stderr, flush=True)
            return 2
        try:
            if stage == VALIDATE:
                if record.status not in ("queued", "validating"):
                    return 0
                validate_uploaded_import(session, record)
                return 0
            if stage == MATERIALIZE:
                if record.status != "committing":
                    return 0
                materialize_uploaded_import(session, record)
                return 0
            print(f"unknown dataset import stage: {stage}", file=sys.stderr, flush=True)
            return 2
        except DatasetSourceChanged as exc:
            mark_worker_failure(
                session,
                import_id,
                code="source_changed",
                message=str(exc),
            )
            return 0
        except DatasetImportNotFound:
            return 2
        except Exception as exc:  # noqa: BLE001 - persisted, never exposed raw
            session.rollback()
            mark_worker_failure(
                session,
                import_id,
                code="worker_failed",
                message="导入 worker 异常退出，请重新上传",
                details={"error_type": type(exc).__name__},
            )
            print(
                f"dataset import worker failed ({type(exc).__name__})",
                file=sys.stderr,
                flush=True,
            )
            return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: python -m app.worker.dataset_import <import_id> validate|materialize", file=sys.stderr)
        return 2
    return _run(argv[0], argv[1])


if __name__ == "__main__":
    raise SystemExit(main())

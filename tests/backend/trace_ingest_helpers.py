"""Builders for M6-02 trace ingestion tests (no API required)."""

from __future__ import annotations

import json
import uuid
from hashlib import sha256
from typing import Any

from app.models import EvalCaseAttempt, EvalCaseRun
from app.seed import ensure_default_scope
from app.services import evaluations
from object_stub import StubObjectStore
from sqlalchemy import select
from sqlalchemy.orm import Session


def create_env(
    session: Session,
    store: StubObjectStore,
    *,
    runtime_key: str | None = "claude-code",
    runtime_version: str | None = "2.1.278",
) -> dict[str, Any]:
    ensure_default_scope(session)
    run, _ = evaluations.create_synthetic_evaluation(
        session,
        name="轨迹导入",
        case_keys=["case-a"],
        idempotency_key=str(uuid.uuid4()),
        created_by="agent",
        runtime_key=runtime_key,
        runtime_version=runtime_version,
    )
    case = session.scalar(select(EvalCaseRun).where(EvalCaseRun.eval_run_id == run.id))
    attempt = session.scalar(
        select(EvalCaseAttempt).where(EvalCaseAttempt.eval_case_run_id == case.id)
    )
    return {"session": session, "store": store, "run": run, "case": case, "attempt": attempt}


def trace_object(
    env: dict[str, Any],
    *,
    logical_path: str,
    content: bytes,
    kind: str = "trace",
    native_format: str | None = "claude-code.session+jsonl",
    content_type: str = "application/x-ndjson",
    digest: str | None = None,
    size: int | None = None,
    attempt_no: int | None = None,
    runtime_version: str = "2.1.278",
) -> dict[str, Any]:
    run = env["run"]
    case = env["case"]
    attempt = env["attempt"]
    number = attempt_no or attempt.attempt_no
    digest = digest or sha256(content).hexdigest()
    object_key = (
        f"evaluations/{run.id}/{attempt.shard_id}/attempt{number}/{case.id}"
        f"/{kind}/{digest}"
    )
    env["store"].add_bytes(object_key, content)
    return {
        "artifact_kind": kind,
        "logical_path": logical_path,
        "object_key": object_key,
        "sha256": digest,
        "size": len(content) if size is None else size,
        "content_type": content_type,
        "native_format": native_format,
        "runtime_version": runtime_version,
        "format_version": "1",
    }


def metadata_object(
    env: dict[str, Any],
    *,
    logical_path: str = "native/subagents/agent-1.meta.json",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content = json.dumps(payload or {"agentId": "sub-1", "sessionId": "sub-1"}).encode()
    return trace_object(
        env,
        logical_path=logical_path,
        content=content,
        kind="trace_metadata",
        native_format="claude-code.subagent.meta+json",
        content_type="application/json",
    )


def jsonl(records: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n").encode()


def attach_manifest(
    session: Session,
    attempt: EvalCaseAttempt,
    objects: list[dict[str, Any]],
    *,
    capture_status: str = "complete",
    reasons: list[str] | None = None,
) -> None:
    manifest = {
        "manifest_version": "1.0",
        "capture_status": capture_status,
        "incomplete_reasons": reasons or [],
        "runtime_version": "2.1.278",
        "format_version": "1",
        "objects": objects,
    }
    attempt.artifact_manifest_json = {
        **(attempt.artifact_manifest_json or {}),
        "trace_manifest": manifest,
        "objects": objects,
    }
    session.commit()

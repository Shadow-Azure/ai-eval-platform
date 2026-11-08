"""Shared builders for case-callback tests (payload shapes per v1 contract)."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from app.services.short_token import issue_short_token

SECRET = "platform-task-secret"

# Re-exported so conftest can share one secret definition with the builders.
EVAL_ENV_SECRET = SECRET


def token_for(env: dict, attempt: int = 1) -> str:
    return issue_short_token(
        {
            "eval_run_id": env["run_id"],
            "shard_id": env["shard"],
            "attempt": attempt,
            "pool_id": "fixed-linux",
        },
        SECRET,
        ttl_seconds=600,
    )


def auth(env: dict, attempt: int = 1) -> dict:
    return {
        "Authorization": f"Bearer {token_for(env, attempt)}",
        "X-Runner-Id": "fixed-linux-eval",
    }


def register_score_object(env: dict, case_run_id: str, size: int = 24) -> dict:
    digest = sha256(f"{case_run_id}:{size}".encode()).hexdigest()
    object_key = f"evaluations/{env['run_id']}/objects/{digest}"
    env["store"].add(object_key, size)
    return {"object_key": object_key, "sha256": digest, "size": size}


def case_url(env: dict, case_run_id: str, action: str) -> str:
    return f"/runner/v1/case-runs/{case_run_id}/{action}"


def started_body(env: dict, case_run_id: str, attempt: int = 1, event_id: str | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": event_id or f"evt_{case_run_id}_started_{attempt}",
        "case_run_id": case_run_id,
        "attempt": attempt,
        "runner_id": "fixed-linux-eval",
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def heartbeat_body(
    case_run_id: str, sequence: int, attempt: int = 1, event_id: str | None = None
) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": event_id or f"evt_{case_run_id}_hb_{sequence}",
        "case_run_id": case_run_id,
        "attempt": attempt,
        "sequence": sequence,
        "phase": "executing",
        "progress": {"completed": 0, "total": 1},
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def completion_body(
    env: dict, case_run_id: str, *, attempt: int = 1, score: dict | None = None
) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": f"evt_{case_run_id}_completed_{attempt}",
        "case_run_id": case_run_id,
        "attempt": attempt,
        "sequence": 2,
        "execution_status": "succeeded",
        "result_status": "resolved",
        "score_manifest": score or register_score_object(env, case_run_id),
        "summary": {"duration_ms": 1200, "input_tokens": 0, "output_tokens": 0},
    }


def failure_body(
    case_run_id: str,
    *,
    attempt: int = 1,
    retryable: bool = False,
    error_code: str = "runtime_error",
    event_id: str | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": event_id or f"evt_{case_run_id}_failed_{attempt}",
        "case_run_id": case_run_id,
        "attempt": attempt,
        "sequence": 3,
        "error_code": error_code,
        "retryable": retryable,
        "error_summary": "合成注入失败",
        "phase": "executing",
    }


def upload_body(env: dict, case_run_id: str) -> dict:
    score = register_score_object(env, case_run_id)
    return {
        "schema_version": "1.0",
        "case_run_id": case_run_id,
        "attempt": 1,
        "objects": [{"artifact_kind": "score", **score, "content_type": "application/json"}],
    }


def upload_urls_body(case_run_id: str) -> dict:
    return {
        "attempt": 1,
        "objects": [
            {
                "artifact_kind": "score",
                "sha256": "a" * 64,
                "size": 24,
                "content_type": "application/json",
            },
            {
                "artifact_kind": "trace",
                "sha256": "b" * 64,
                "size": 120,
                "content_type": "application/x-ndjson",
            },
        ],
    }

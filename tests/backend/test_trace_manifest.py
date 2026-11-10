"""M6-01: multi-file native trace manifests on the runner callbacks.

The runner reports platform-owned objects; the backend verifies attempt
ownership through the callback token, safe logical paths, digests and sizes,
collapses repeated digests without ambiguity, and records whether capture is
complete. Legacy single-file `trace_manifest` payloads stay readable.
"""

from __future__ import annotations

from hashlib import sha256

from case_helpers import (
    auth,
    case_url,
    completion_body,
    failure_body,
    register_score_object,
    started_body,
)
from fastapi.testclient import TestClient


def _trace_object(
    env: dict,
    logical_path: str,
    content: bytes,
    *,
    kind: str = "trace",
    content_type: str = "application/x-ndjson",
) -> dict:
    digest = sha256(content).hexdigest()
    object_key = f"evaluations/{env['run_id']}/attempt1/{digest}"
    env["store"].add_bytes(object_key, content)
    return {
        "artifact_kind": kind,
        "logical_path": logical_path,
        "object_key": object_key,
        "sha256": digest,
        "size": len(content),
        "content_type": content_type,
        "native_format": "claude-code.session+jsonl" if kind == "trace" else "claude-code.subagent.meta+json",
        "runtime_version": "2.1.278",
        "format_version": "1",
    }


def _trace_manifest(objects: list[dict], *, status: str = "complete", reasons: list[str] | None = None) -> dict:
    return {
        "manifest_version": "1.0",
        "capture_status": status,
        "incomplete_reasons": reasons or [],
        "runtime_version": "2.1.278",
        "format_version": "1",
        "objects": objects,
    }


def _completion_with_trace(env: dict, case_run_id: str, trace_manifest: dict) -> dict:
    body = completion_body(env, case_run_id)
    body["trace_manifest"] = trace_manifest
    return body


def _attempt(env: dict, case_run_id: str, attempt_no: int = 1):
    env["session"].expire_all()
    from app.models import EvalCaseAttempt

    return (
        env["session"]
        .query(EvalCaseAttempt)
        .filter(EvalCaseAttempt.eval_case_run_id == case_run_id)
        .filter(EvalCaseAttempt.attempt_no == attempt_no)
        .one()
    )


def test_multifile_trace_upload_and_completion_link_every_object(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    score = register_score_object(env, case_run_id)
    objects = [
        _trace_object(env, "native/projects/-testbed/session-1.jsonl", b'{"type":"user"}\n'),
        _trace_object(
            env,
            "native/projects/-testbed/subagents/agent-a1.jsonl",
            b'{"agentId":"a1","isSidechain":true}\n',
        ),
        _trace_object(
            env,
            "native/projects/-testbed/subagents/agent-a1.meta.json",
            b'{"agentId":"a1","toolUseId":"tool-1"}\n',
            kind="trace_metadata",
            content_type="application/json",
        ),
        _trace_object(
            env,
            "diagnostics/stderr.log",
            b"gateway warning\n",
            kind="trace_diagnostic",
            content_type="text/plain",
        ),
    ]
    uploads = {
        "schema_version": "1.0",
        "case_run_id": case_run_id,
        "attempt": 1,
        "objects": [{"artifact_kind": "score", **score, "content_type": "application/json"}, *objects],
    }
    response = client.post(case_url(env, case_run_id, "uploads"), json=uploads, headers=auth(env))
    assert response.status_code == 200, response.text

    completed = client.post(
        case_url(env, case_run_id, "completed"),
        json=_completion_with_trace(env, case_run_id, _trace_manifest(objects)),
        headers=auth(env),
    )
    assert completed.status_code == 200, completed.text

    attempt = _attempt(env, case_run_id)
    assert attempt.status == "succeeded"
    manifest = attempt.artifact_manifest_json
    assert len(manifest["objects"]) == 5
    assert manifest["trace_manifest"]["capture_status"] == "complete"
    assert {item["logical_path"] for item in manifest["trace_manifest"]["objects"]} == {
        "native/projects/-testbed/session-1.jsonl",
        "native/projects/-testbed/subagents/agent-a1.jsonl",
        "native/projects/-testbed/subagents/agent-a1.meta.json",
        "diagnostics/stderr.log",
    }
    from app.models import EvalCaseRun

    env["session"].expire_all()
    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.trace_status == "ready"


def test_repeated_digest_is_one_object_and_never_ambiguous(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    score = register_score_object(env, case_run_id)
    first = _trace_object(env, "native/projects/-testbed/session-1.jsonl", b'{"same":true}\n')
    repeated_same_path = dict(first)
    repeated_other_path = dict(first)
    repeated_other_path["logical_path"] = "native/projects/-testbed/session-copy.jsonl"
    body = {
        "schema_version": "1.0",
        "case_run_id": case_run_id,
        "attempt": 1,
        "objects": [
            {"artifact_kind": "score", **score, "content_type": "application/json"},
            first,
            repeated_same_path,
            repeated_other_path,
        ],
    }
    response = client.post(case_url(env, case_run_id, "uploads"), json=body, headers=auth(env))
    assert response.status_code == 200, response.text
    attempt = _attempt(env, case_run_id)
    assert len(attempt.artifact_manifest_json["objects"]) == 2

    conflicting = dict(first)
    conflicting["sha256"] = "f" * 64
    conflicting["object_key"] = f"evaluations/{env['run_id']}/attempt1/{'f' * 64}"
    second = {
        "schema_version": "1.0",
        "case_run_id": case_run_id,
        "attempt": 1,
        "objects": [first, conflicting],
    }
    rejected = client.post(case_url(env, case_run_id, "uploads"), json=second, headers=auth(env))
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error"]["code"] == "manifest_conflict"


def test_illegal_logical_paths_are_rejected(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    score = register_score_object(env, case_run_id)
    traverse = _trace_object(env, "native/safe.jsonl", b"{}\n")
    traverse["logical_path"] = "../etc/passwd"
    body = {
        "schema_version": "1.0",
        "case_run_id": case_run_id,
        "attempt": 1,
        "objects": [{"artifact_kind": "score", **score, "content_type": "application/json"}, traverse],
    }
    response = client.post(case_url(env, case_run_id, "uploads"), json=body, headers=auth(env))
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "manifest_invalid"

    absolute = _trace_object(env, "native/safe.jsonl", b"{}\n")
    absolute["logical_path"] = "/testbed/.claude/projects/session.jsonl"
    body["objects"] = [{"artifact_kind": "score", **score, "content_type": "application/json"}, absolute]
    rejected = client.post(case_url(env, case_run_id, "uploads"), json=body, headers=auth(env))
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["error"]["code"] == "contract_invalid"


def test_legacy_single_trace_manifest_is_still_readable(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    legacy = _trace_object(env, "trace/claude-stream.jsonl", b'{"type":"result"}\n')
    body = completion_body(env, case_run_id)
    body["trace_manifest"] = {
        "object_key": legacy["object_key"],
        "sha256": legacy["sha256"],
        "size": legacy["size"],
    }
    response = client.post(case_url(env, case_run_id, "completed"), json=body, headers=auth(env))
    assert response.status_code == 200, response.text

    attempt = _attempt(env, case_run_id)
    assert attempt.artifact_manifest_json["trace_manifest"]["object_key"] == legacy["object_key"]
    from app.models import EvalCaseRun

    env["session"].expire_all()
    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.trace_status == "ready"


def test_stdout_only_capture_is_incomplete_never_ready(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    stdout = _trace_object(
        env,
        "diagnostics/stdout.jsonl",
        b'{"type":"system","session_id":"s1"}\n',
        kind="trace_diagnostic",
    )
    manifest = _trace_manifest(
        [stdout], status="partial", reasons=["native_session_missing"]
    )
    response = client.post(
        case_url(env, case_run_id, "completed"),
        json=_completion_with_trace(env, case_run_id, manifest),
        headers=auth(env),
    )
    assert response.status_code == 200, response.text

    attempt = _attempt(env, case_run_id)
    assert attempt.artifact_manifest_json["trace_manifest"]["capture_status"] == "partial"
    assert attempt.artifact_manifest_json["trace_manifest"]["incomplete_reasons"] == [
        "native_session_missing"
    ]
    from app.models import EvalCaseRun

    env["session"].expire_all()
    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.trace_status == "incomplete"


def test_missing_trace_object_is_artifact_missing(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    ghost = {
        "artifact_kind": "trace",
        "logical_path": "native/projects/-testbed/s1.jsonl",
        "object_key": f"evaluations/{env['run_id']}/attempt1/{'0' * 64}",
        "sha256": "0" * 64,
        "size": 10,
        "content_type": "application/x-ndjson",
    }
    response = client.post(
        case_url(env, case_run_id, "completed"),
        json=_completion_with_trace(env, case_run_id, _trace_manifest([ghost])),
        headers=auth(env),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "artifact_missing"
    assert _attempt(env, case_run_id).status == "artifact_missing"


def test_retry_attempt_keeps_its_own_trace_manifest(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    client.post(case_url(env, case_run_id, "started"), json=started_body(env, case_run_id), headers=auth(env))
    first_trace = _trace_object(env, "native/projects/-testbed/s1.jsonl", b'{"attempt":1}\n')
    uploads = {
        "schema_version": "1.0",
        "case_run_id": case_run_id,
        "attempt": 1,
        "objects": [first_trace],
    }
    assert client.post(case_url(env, case_run_id, "uploads"), json=uploads, headers=auth(env)).status_code == 200
    attempt_one_manifest = _attempt(env, case_run_id).artifact_manifest_json

    failed = client.post(
        case_url(env, case_run_id, "failed"),
        json=failure_body(case_run_id, retryable=True, error_code="callback_retryable"),
        headers=auth(env),
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["retry_queued"] is True

    env["session"].expire_all()
    assert _attempt(env, case_run_id, 1).artifact_manifest_json == attempt_one_manifest
    retry = _attempt(env, case_run_id, 2)
    assert retry.artifact_manifest_json == {}
    assert retry.id != _attempt(env, case_run_id, 1).id


def test_complete_claim_without_native_session_is_downgraded(eval_env, client: TestClient) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    stdout = _trace_object(
        env,
        "diagnostics/stdout.jsonl",
        b'{"type":"system","session_id":"s1"}\n',
        kind="trace_diagnostic",
    )
    response = client.post(
        case_url(env, case_run_id, "completed"),
        json=_completion_with_trace(
            env, case_run_id, _trace_manifest([stdout], status="complete")
        ),
        headers=auth(env),
    )
    assert response.status_code == 200, response.text
    attempt = _attempt(env, case_run_id)
    stored = attempt.artifact_manifest_json["trace_manifest"]
    assert stored["capture_status"] == "partial"
    assert "native_session_missing" in stored["incomplete_reasons"]
    from app.models import EvalCaseRun

    env["session"].expire_all()
    case = env["session"].query(EvalCaseRun).filter(EvalCaseRun.id == case_run_id).one()
    assert case.trace_status == "incomplete"

"""POST /runner/v1/case-runs/{id}/upload-urls: presigned OSS PUT addresses.

Design 04 §5: the uploads exchange returns platform-owned object keys and a
time-limited presigned PUT per object; the runner uploads bytes, then reports
the objects back through the (existing) uploads callback where existence is
verified. Object keys are platform-generated — the runner never picks prefixes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from case_helpers import auth, upload_urls_body


def test_upload_urls_returns_platform_keys_and_presigned_puts(eval_env, client) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]

    response = client.post(
        f"/runner/v1/case-runs/{case_run_id}/upload-urls",
        json=upload_urls_body(case_run_id),
        headers=auth(env),
    )
    assert response.status_code == 200, response.text
    uploads = response.json()["uploads"]
    assert len(uploads) == 2
    for item in uploads:
        assert item["method"] == "PUT"
        assert item["upload_url"].startswith(f"{env['store'].endpoint}/eval-artifacts/")
        assert "X-Amz-Signature" in item["upload_url"]
        assert datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00")) > datetime.now(UTC)
    # Platform owns keys; the shard/attempt/case path is enforced.
    assert uploads[0]["object_key"].startswith(
        f"evaluations/{env['run_id']}/{env['shard']}/attempt1/{case_run_id}/score/"
    )
    assert uploads[1]["object_key"].endswith("b" * 64)


def test_upload_urls_requires_object_store(eval_env, client, configure_settings) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    configure_settings(S3_ENDPOINT="", S3_ACCESS_KEY="", S3_SECRET_KEY="")

    response = client.post(
        f"/runner/v1/case-runs/{case_run_id}/upload-urls",
        json=upload_urls_body(case_run_id),
        headers=auth(env),
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "object_store_unavailable"


def test_upload_urls_rejects_stale_attempt_and_unknown_case(eval_env, client) -> None:
    env = eval_env
    case_run_id = env["cases"]["case-a"]
    body = upload_urls_body(case_run_id)
    body["attempt"] = 7

    stale = client.post(
        f"/runner/v1/case-runs/{case_run_id}/upload-urls", json=body, headers=auth(env, attempt=7)
    )
    assert stale.status_code == 409

    unknown = client.post(
        "/runner/v1/case-runs/ecr_ghost/upload-urls",
        json=upload_urls_body("ecr_ghost"),
        headers=auth(env),
    )
    assert unknown.status_code == 404


def test_upload_urls_full_loop_put_then_callbacks(eval_env, client) -> None:
    """Presign → runner PUT (HTTP) → uploads callback → completed with the
    platform-owned keys: the exact sequence the real OSS path exercises."""

    env = eval_env
    case_run_id = env["cases"]["case-a"]
    import json
    from urllib.request import Request, urlopen

    issued = client.post(
        f"/runner/v1/case-runs/{case_run_id}/upload-urls",
        json=upload_urls_body(case_run_id),
        headers=auth(env),
    ).json()["uploads"]

    payload = json.dumps({"hello": "case"}).encode()
    for item in issued:
        request = Request(item["upload_url"], data=payload, method="PUT")
        with urlopen(request, timeout=5) as response:
            assert response.status == 200
    # The stub store records every PUT (path → byte length), like real storage.

    uploads_payload = {
        "schema_version": "1.0",
        "case_run_id": case_run_id,
        "attempt": 1,
        "objects": [
            {
                "artifact_kind": item["artifact_kind"],
                "object_key": item["object_key"],
                "sha256": item["sha256"],
                "size": len(payload),
                "content_type": "application/json",
            }
            for item in issued
        ],
    }
    uploads = client.post(
        f"/runner/v1/case-runs/{case_run_id}/uploads", json=uploads_payload, headers=auth(env)
    )
    assert uploads.status_code == 200, uploads.text

    completion = {
        "schema_version": "1.0",
        "event_id": f"evt_{case_run_id}_completed",
        "case_run_id": case_run_id,
        "attempt": 1,
        "sequence": 2,
        "execution_status": "succeeded",
        "result_status": "resolved",
        "score_manifest": {
            "object_key": issued[0]["object_key"],
            "sha256": issued[0]["sha256"],
            "size": len(payload),
        },
        "summary": {"duration_ms": 5, "input_tokens": 0, "output_tokens": 0},
    }
    completed = client.post(
        f"/runner/v1/case-runs/{case_run_id}/completed", json=completion, headers=auth(env)
    )
    assert completed.status_code == 200, completed.text

#!/usr/bin/env node
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { verifyToken } from "../lib/auth.mjs";
import { postCallback } from "../lib/callback-client.mjs";
import { requireValid, validateDispatch, validateManifest, validateStarted } from "../lib/contracts.mjs";
import { DispatchGuard } from "../lib/dispatch-guard.mjs";
import { ContractError } from "../lib/errors.mjs";
import { LocalObjectStore } from "../lib/object-store.mjs";

function parseArgs(argv) {
  const args = { dispatch: null, stateDir: ".runner-state", objectRoot: ".runner-objects", token: null, caseRunId: null };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (arg === "--dispatch") { args.dispatch = next; index += 1; }
    else if (arg === "--state-dir") { args.stateDir = next; index += 1; }
    else if (arg === "--object-store-root") { args.objectRoot = next; index += 1; }
    else if (arg === "--token") { args.token = next; index += 1; }
    else if (arg === "--case") { args.caseRunId = next; index += 1; }
    else if (arg === "--help") { args.help = true; }
    else throw new Error(`未知参数：${arg}`);
  }
  return args;
}

function fail(message, code = "runtime_error", exitCode = 2) {
  console.error(JSON.stringify({ status: "rejected", error_code: code, message }, null, 2));
  process.exit(exitCode);
}

/**
 * Fetch the manifest. HTTP(S) responses also carry the platform-issued
 * short-term token in X-Runner-Task-Token (M2-05); file:// manifests are the
 * M2-04 local mode and require a token from the environment instead.
 */
async function fetchManifest(manifestUrl) {
  if (manifestUrl.startsWith("file://")) {
    return { bytes: readFileSync(new URL(manifestUrl)), tokenHeader: null };
  }
  const response = await fetch(manifestUrl, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`manifest 拉取失败：HTTP ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  return { bytes, tokenHeader: response.headers.get("x-runner-task-token") };
}

/** Decode v1.<body>.<sig> claims without signature verification (dev mode). */
function parseTokenClaims(token) {
  const parts = String(token ?? "").split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return null;
  try {
    return JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
  } catch {
    return null;
  }
}

function sha256(text) {
  return `sha256:${createHash("sha256").update(text).digest("hex")}`;
}

function deterministicCase(dispatch, item) {
  const digest = createHash("sha256").update(`${dispatch.dispatch_key}\n${item.case_key}`).digest();
  const value = digest.readUInt32BE(0) % 1000;
  const score = {
    case_run_id: item.case_run_id,
    case_key: item.case_key,
    execution_status: "succeeded",
    result_status: "resolved",
    resolved: true,
    checks: [{ name: "deterministic-value", expected: value, actual: value }],
  };
  const trace = [
    { type: "case.start", case_key: item.case_key, resolved: false, value: null },
    { type: "case.end", case_key: item.case_key, resolved: true, value },
  ]
    .map((line) => JSON.stringify(line))
    .join("\n") + "\n";
  return { score, trace };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.dispatch) {
    console.log("用法：node runner/scripts/synthetic-task.mjs --dispatch <file> [--state-dir dir] [--object-store-root dir] [--token token] [--case caseRunId]");
    return 0;
  }

  const secret = process.env.RUNNER_TASK_SECRET;
  const presignUploads = process.env.RUNNER_PRESIGN_UPLOADS === "1";

  let dispatch;
  try {
    dispatch = JSON.parse(readFileSync(resolve(args.dispatch), "utf8"));
  } catch (error) {
    fail(`dispatch 读取失败：${error.message}`, "manifest_invalid");
  }

  const dispatchCheck = validateDispatch(dispatch);
  if (!dispatchCheck.ok) fail(`dispatch 校验失败：${dispatchCheck.errors.join("; ")}`, "manifest_invalid");

  if (Date.parse(dispatch.expires_at) < Date.now()) fail("dispatch 已过期", "token_invalid");

  // Manifest fetch doubles as the token issuance point for the platform mode.
  const { bytes: manifestBytes, tokenHeader } = await fetchManifest(dispatch.manifest_url).catch((error) =>
    fail(error.message, "manifest_invalid")
  );
  if (sha256(manifestBytes) !== dispatch.manifest_digest) {
    fail("manifest digest 不匹配", "manifest_digest_mismatch");
  }

  // Explicit --token beats env, env beats the platform header; empty values
  // fall through (workflow files may export empty strings).
  const token = args.token || process.env.RUNNER_TASK_TOKEN || tokenHeader;
  if (!token) fail("缺少任务 token（环境 RUNNER_TASK_TOKEN 或平台 X-Runner-Task-Token）", "token_invalid");

  if (secret) {
    try {
      verifyToken(token, {
        secret,
        eval_run_id: dispatch.eval_run_id,
        shard_id: dispatch.shard_id,
        attempt: dispatch.attempt,
      });
    } catch (error) {
      fail(error.message, error.code ?? "token_invalid");
    }
  } else {
    // No shared secret configured (platform token over loopback): still
    // enforce the bindings the platform claims for this dispatch.
    const claims = parseTokenClaims(token);
    if (!claims) fail("token 无法解析", "token_invalid");
    for (const key of ["eval_run_id", "shard_id", "attempt"]) {
      if (claims[key] !== dispatch[key]) fail(`token 与 ${key} 不匹配`, "token_invalid");
    }
  }

  let manifest;
  try {
    manifest = JSON.parse(manifestBytes.toString("utf8"));
  } catch (error) {
    fail(`manifest 不是 JSON：${error.message}`, "manifest_invalid");
  }
  const manifestCheck = validateManifest(manifest);
  if (!manifestCheck.ok) fail(`manifest 校验失败：${manifestCheck.errors.join("; ")}`, "manifest_invalid");

  for (const key of ["eval_run_id", "shard_id", "attempt", "dispatch_key"]) {
    if (manifest[key] !== dispatch[key]) fail(`manifest.${key} 与 dispatch 不一致`, "attempt_mismatch");
  }
  if (Date.parse(manifest.deadline) < Date.now()) fail("manifest 已过 deadline", "token_invalid");

  let cases = manifest.cases;
  if (args.caseRunId) {
    cases = cases.filter((item) => item.case_run_id === args.caseRunId);
    if (cases.length === 0) fail(`manifest 中不存在 case ${args.caseRunId}`, "manifest_invalid");
  }

  const guard = new DispatchGuard(resolve(args.stateDir));
  const claim = guard.claim(dispatch.dispatch_key, {
    eval_run_id: dispatch.eval_run_id,
    shard_id: dispatch.shard_id,
    attempt: dispatch.attempt,
  });
  if (!claim.claimed) {
    console.log(JSON.stringify({ status: "duplicate_dispatch", dispatch_key: dispatch.dispatch_key }));
    return 0;
  }

  const store = new LocalObjectStore(resolve(args.objectRoot));
  const objects = [];

  const requestUploadUrls = async (item, pendingObjects) => {
    const response = await fetch(
      `${manifest.callback_base_url}/runner/v1/case-runs/${item.case_run_id}/upload-urls`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${token}`,
          "idempotency-key": `evt_${item.case_run_id}_upload_urls_${dispatch.attempt}`,
        },
        body: JSON.stringify({
          attempt: dispatch.attempt,
          objects: pendingObjects.map(({ artifact_kind, sha256: digest, size, content_type }) => ({
            artifact_kind,
            sha256: digest,
            size,
            content_type,
          })),
        }),
        signal: AbortSignal.timeout(10000),
      },
    );
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.uploads?.length) {
      throw Object.assign(new Error(`upload-urls 换取失败：HTTP ${response.status}`), {
        code: "upload_failed",
        httpStatus: response.status,
      });
    }
    return payload.uploads;
  };

  try {
    for (const item of cases) {
      const started = requireValid("started", {
        schema_version: "1.0",
        event_id: `evt_${dispatch.shard_id}_${item.case_run_id}_started_${dispatch.attempt}`,
        case_run_id: item.case_run_id,
        attempt: dispatch.attempt,
        // runner_id is optional in the contract; omit (not undefined) when the
        // agent env does not provide one, or schema validation rejects it.
        ...(process.env.RUNNER_ID ? { runner_id: process.env.RUNNER_ID } : {}),
        occurred_at: new Date().toISOString(),
      });
      const startedResult = await postCallback(
        `${manifest.callback_base_url}/runner/v1/case-runs/${item.case_run_id}/started`,
        started,
        { eventId: started.event_id, token },
      );
      if (!startedResult.ok && !startedResult.stale) {
        fail(
          `started 回调失败：HTTP ${startedResult.status}`,
          startedResult.status === 0 || startedResult.status >= 500 || startedResult.status === 429
            ? "callback_retryable"
            : "callback_permanent",
          1,
        );
      }

      const heartbeat = requireValid("heartbeat", {
        schema_version: "1.0",
        event_id: `evt_${dispatch.shard_id}_${item.case_run_id}_heartbeat_1`,
        case_run_id: item.case_run_id,
        attempt: dispatch.attempt,
        sequence: 1,
        phase: "executing",
        progress: { completed: 0, total: 1 },
        occurred_at: new Date().toISOString(),
      });
      await postCallback(`${manifest.callback_base_url}/runner/v1/case-runs/${item.case_run_id}/heartbeat`, heartbeat, {
        eventId: heartbeat.event_id,
        token,
      });

      const { score, trace } = deterministicCase(dispatch, item);
      const scoreObject = store.putJson(score);
      const traceObject = store.putBytes(Buffer.from(trace, "utf8"), { contentType: "application/x-ndjson" });
      const pendingObjects = [
        { artifact_kind: "score", ...scoreObject },
        { artifact_kind: "trace", ...traceObject },
      ];

      let reportedObjects = pendingObjects;
      if (presignUploads) {
        const uploads = await requestUploadUrls(item, pendingObjects);
        for (const upload of uploads) {
          const pending = pendingObjects.find((candidate) => candidate.artifact_kind === upload.artifact_kind);
          if (!pending) {
            throw Object.assign(new Error(`upload-urls 返回未知 artifact_kind ${upload.artifact_kind}`), {
              code: "upload_failed",
            });
          }
          const bytes = store.get(pending.object_key);
          const putResponse = await fetch(upload.upload_url, {
            method: "PUT",
            headers: upload.headers ?? {},
            body: bytes,
            signal: AbortSignal.timeout(30000),
          });
          if (!putResponse.ok) {
            throw Object.assign(new Error(`对象上传失败：HTTP ${putResponse.status}`), {
              code: "upload_failed",
            });
          }
        }
        reportedObjects = uploads.map((upload) => ({
          artifact_kind: upload.artifact_kind,
          object_key: upload.object_key,
          sha256: upload.sha256,
          size: upload.size,
          content_type: upload.headers?.["content-type"] ?? "application/octet-stream",
        }));
      }
      objects.push(...reportedObjects);

      const upload = requireValid("upload", {
        schema_version: "1.0",
        case_run_id: item.case_run_id,
        attempt: dispatch.attempt,
        objects: reportedObjects.map(({ artifact_kind, object_key, sha256: digest, size, content_type }) => ({
          artifact_kind,
          object_key,
          sha256: digest,
          size,
          content_type,
        })),
      });
      const uploadResult = await postCallback(
        `${manifest.callback_base_url}/runner/v1/case-runs/${item.case_run_id}/uploads`,
        upload,
        { eventId: `evt_${item.case_run_id}_uploads_${dispatch.attempt}`, token },
      );
      if (!uploadResult.ok) {
        const retryable = uploadResult.status === 0 || uploadResult.status >= 500 || uploadResult.status === 429;
        await postCallback(`${manifest.callback_base_url}/runner/v1/case-runs/${item.case_run_id}/failed`, requireValid("failure", {
          schema_version: "1.0",
          event_id: `evt_${item.case_run_id}_failed_${dispatch.attempt}`,
          case_run_id: item.case_run_id,
          attempt: dispatch.attempt,
          sequence: 2,
          error_code: retryable ? "callback_retryable" : "upload_failed",
          retryable,
          error_summary: `上传清单回调失败：HTTP ${uploadResult.status}`,
          phase: "uploading",
        }), { eventId: `evt_${item.case_run_id}_failed_${dispatch.attempt}`, token });
        fail(`上传清单回调失败：HTTP ${uploadResult.status}`, retryable ? "callback_retryable" : "upload_failed", 1);
      }

      const scoreReport = reportedObjects.find((object) => object.artifact_kind === "score");
      const traceReport = reportedObjects.find((object) => object.artifact_kind === "trace");
      const completion = requireValid("completion", {
        schema_version: "1.0",
        event_id: `evt_${dispatch.shard_id}_${item.case_run_id}_completed`,
        case_run_id: item.case_run_id,
        attempt: dispatch.attempt,
        sequence: 2,
        execution_status: "succeeded",
        result_status: "resolved",
        score_manifest: {
          object_key: scoreReport.object_key,
          sha256: scoreReport.sha256,
          size: scoreReport.size,
        },
        ...(traceReport
          ? {
              trace_manifest: {
                object_key: traceReport.object_key,
                sha256: traceReport.sha256,
                size: traceReport.size,
              },
            }
          : {}),
        summary: { duration_ms: 0, input_tokens: 0, output_tokens: 0 },
      });
      const result = await postCallback(
        `${manifest.callback_base_url}/runner/v1/case-runs/${item.case_run_id}/completed`,
        completion,
        { eventId: completion.event_id, token },
      );
      if (!result.ok) {
        if (result.stale) {
          console.error(JSON.stringify({ status: "stale_attempt", case_run_id: item.case_run_id, attempt: dispatch.attempt }));
          process.exit(3);
        }
        const retryable = result.status === 0 || result.status >= 500 || result.status === 429;
        await postCallback(`${manifest.callback_base_url}/runner/v1/case-runs/${item.case_run_id}/failed`, requireValid("failure", {
          schema_version: "1.0",
          event_id: `evt_${item.case_run_id}_failed_${dispatch.attempt}`,
          case_run_id: item.case_run_id,
          attempt: dispatch.attempt,
          sequence: 3,
          error_code: retryable ? "callback_retryable" : "callback_permanent",
          retryable,
          error_summary: `完成回调失败：HTTP ${result.status}`,
          phase: "scoring",
        }), { eventId: `evt_${item.case_run_id}_failed_${dispatch.attempt}`, token });
        fail(`完成回调失败：HTTP ${result.status}`, retryable ? "callback_retryable" : "callback_permanent", 1);
      }
    }

    guard.complete(claim.claimPath);
    console.log(JSON.stringify({
      status: "completed",
      eval_run_id: dispatch.eval_run_id,
      shard_id: dispatch.shard_id,
      attempt: dispatch.attempt,
      cases: cases.length,
      objects: objects.map(({ object_key, sha256: digest, size }) => ({ object_key, sha256: digest, size })),
    }));
    return 0;
  } catch (error) {
    if (error instanceof ContractError) {
      fail(error.message, error.code, 1);
    }
    if (error?.code === "callback_retryable" || error?.code === "callback_permanent" || error?.code === "upload_failed") {
      const retryable = error.code !== "callback_permanent";
      await postCallback(`${manifest.callback_base_url}/runner/v1/case-runs/${cases[0]?.case_run_id ?? "unknown"}/failed`, requireValid("failure", {
        schema_version: "1.0",
        event_id: `evt_${dispatch.shard_id}_failed_${dispatch.attempt}`,
        case_run_id: cases[0]?.case_run_id ?? "unknown",
        attempt: dispatch.attempt,
        sequence: 3,
        error_code: error.code,
        retryable,
        error_summary: String(error.message ?? error).slice(0, 1000),
        phase: "uploading",
      }), { eventId: `evt_${dispatch.shard_id}_failed_${dispatch.attempt}`, token }).catch(() => {});
      fail(String(error.message ?? error), error.code, 1);
    }
    fail(String(error.message ?? error), "runtime_error", 1);
  }
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error(error);
  process.exit(1);
});

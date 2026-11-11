#!/usr/bin/env node
/**
 * M6-06 fixed-runner trace E2E executor.
 *
 * Unlike ``real-task.mjs`` this intentionally does not clone/score a SWE-bench
 * repository. It keeps the production runner contracts (manifest, started,
 * heartbeat, presigned upload, uploads, completion/failure) while using a
 * deterministic provider gateway to make Claude Code 2.1.278 spawn two real
 * child agents. The resulting trace files are the real CLI-native files.
 */
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { postCallback } from "../lib/callback-client.mjs";
import {
  requireValid,
  validateDispatch,
  validateManifest,
} from "../lib/contracts.mjs";
import { fetchManifest } from "../lib/manifest-source.mjs";
import { LocalObjectStore } from "../lib/object-store.mjs";
import { runClaudeCode } from "../lib/runtime-claude-code.mjs";
import { traceE2EClaudeEnv } from "../lib/trace-e2e.mjs";
import {
  collectTraceBundle,
  dedupeTraceObjects,
  traceManifestFromBundle,
} from "../lib/trace-capture.mjs";

function parseArgs(argv) {
  const args = {
    dispatch: null,
    stateDir: null,
    objectRoot: null,
    terminal: "succeeded",
    profile: "real",
    gatewayUrl: null,
    gatewayToken: "m6-e2e-gateway-token",
    model: "claude-sonnet-4-20250514",
    evidence: null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (arg === "--dispatch") {
      args.dispatch = next;
      index += 1;
    } else if (arg === "--state-dir") {
      args.stateDir = next;
      index += 1;
    } else if (arg === "--object-root") {
      args.objectRoot = next;
      index += 1;
    } else if (arg === "--terminal") {
      args.terminal = next;
      index += 1;
    } else if (arg === "--profile") {
      args.profile = next;
      index += 1;
    } else if (arg === "--gateway-url") {
      args.gatewayUrl = next;
      index += 1;
    } else if (arg === "--gateway-token") {
      args.gatewayToken = next;
      index += 1;
    } else if (arg === "--model") {
      args.model = next;
      index += 1;
    } else if (arg === "--evidence") {
      args.evidence = next;
      index += 1;
    } else if (arg === "--help") {
      args.help = true;
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return args;
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function putPresigned(upload, bytes) {
  let lastStatus = 0;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      const response = await fetch(upload.upload_url, {
        method: "PUT",
        headers: upload.headers ?? {},
        body: bytes,
        signal: AbortSignal.timeout(30000),
      });
      lastStatus = response.status;
      if (response.ok) return;
    } catch {
      lastStatus = 0;
    }
    if (attempt < 5) await new Promise((resolveWait) => setTimeout(resolveWait, attempt * 250));
  }
  throw new Error(`presigned PUT failed: HTTP ${lastStatus}`);
}

async function callback(url, payload, eventId, token) {
  const result = await postCallback(url, payload, { eventId, token, timeoutMs: 15000 });
  if (!result.ok) {
    throw new Error(`callback failed: ${eventId} HTTP ${result.status} ${result.body}`);
  }
  return result;
}

function readClaudeVersion() {
  const env = traceE2EClaudeEnv();
  const result = spawnSync("claude", ["--version"], {
    encoding: "utf8",
    env,
    timeout: 15000,
  });
  if (result.error) throw new Error(`claude --version failed: ${result.error.message}`);
  if (result.status !== 0) {
    throw new Error(`claude --version exited ${result.status}: ${(result.stderr || "").slice(-300)}`);
  }
  const match = /(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)/.exec(result.stdout || "");
  if (!match) throw new Error(`claude --version did not report a semantic version: ${result.stdout}`);
  return match[1];
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.dispatch || !args.gatewayUrl || !args.evidence) {
    console.log(
      "usage: node runner/scripts/trace-e2e-runner.mjs --dispatch FILE --gateway-url URL --evidence FILE [--terminal succeeded|failed] [--profile real|degraded]",
    );
    return 0;
  }
  const dispatch = JSON.parse(readFileSync(resolve(args.dispatch), "utf8"));
  const dispatchCheck = validateDispatch(dispatch);
  if (!dispatchCheck.ok) throw new Error(`dispatch invalid: ${dispatchCheck.errors.join("; ")}`);

  const { bytes: manifestBytes, tokenHeader } = await fetchManifest(dispatch.manifest_url);
  if (`sha256:${sha256(manifestBytes)}` !== dispatch.manifest_digest) {
    throw new Error("manifest digest mismatch");
  }
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  const manifestCheck = validateManifest(manifest);
  if (!manifestCheck.ok) throw new Error(`manifest invalid: ${manifestCheck.errors.join("; ")}`);
  if (!tokenHeader) throw new Error("manifest response did not issue a runner token");
  const item = manifest.cases[0];
  if (!item) throw new Error("manifest has no case");

  mkdirSync(resolve(args.stateDir || join(tmpdir(), `trace-e2e-state-${process.pid}`)), {
    recursive: true,
  });
  const objectRoot = resolve(args.objectRoot || join(tmpdir(), `trace-e2e-objects-${process.pid}`));
  const workspace = mkdtempSync(join(tmpdir(), "m6-trace-e2e-"));
  const runtimeHome = join(workspace, ".runtime-home");
  mkdirSync(join(workspace, "trace"), { recursive: true });
  mkdirSync(join(runtimeHome, ".claude"), { recursive: true });

  const callbackBase = manifest.callback_base_url.replace(/\/+$/, "");
  const runnerId = process.env.RUNNER_ID || "fixed-linux-eval";
  const started = requireValid("started", {
    schema_version: "1.0",
    event_id: `evt_${dispatch.shard_id}_${item.case_run_id}_started_${dispatch.attempt}`,
    case_run_id: item.case_run_id,
    attempt: dispatch.attempt,
    runner_id: runnerId,
    occurred_at: new Date().toISOString(),
  });
  await callback(
    `${callbackBase}/runner/v1/case-runs/${item.case_run_id}/started`,
    started,
    started.event_id,
    tokenHeader,
  );

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
  await callback(
    `${callbackBase}/runner/v1/case-runs/${item.case_run_id}/heartbeat`,
    heartbeat,
    heartbeat.event_id,
    tokenHeader,
  );

  let run = { events: [] };
  try {
    let bundle;
    if (args.profile === "degraded") {
      const stdout = Buffer.from(
        `${JSON.stringify({ type: "system", subtype: "init", session_id: "degraded-no-native" })}\n`,
      );
      const unknown = Buffer.from(
        `${JSON.stringify({ type: "unknown", note: "unknown future native format" })}\n`,
      );
      bundle = {
        schema_version: "1.0",
        runtime_key: "claude-code",
        runtime_version: "2.1.278",
        adapter_version: "claude-code-1.0",
        capture_status: "incomplete",
        incomplete_reasons: ["native_session_missing", "stdout_only"],
        files: [
          {
            logical_path: "diagnostics/stdout.jsonl",
            artifact_kind: "trace_diagnostic",
            content_type: "application/x-ndjson",
            native_format: "claude-code.stdout.stream-json",
            format_version: "1",
            runtime_version: "2.1.278",
            bytes: stdout,
          },
          {
            logical_path: "native/projects/degraded/unknown.trace",
            artifact_kind: "trace",
            content_type: "text/plain",
            native_format: "claude-code.unknown",
            format_version: "99",
            runtime_version: "2.1.278",
            bytes: unknown,
          },
        ],
      };
    } else {
      const runtimeVersion = readClaudeVersion();
      if (runtimeVersion !== "2.1.278") {
        throw new Error(`unexpected Claude Code version: ${runtimeVersion}`);
      }
      run = await runClaudeCode({
        prompt:
          "Launch exactly two Agent subagents in parallel. Alpha must return M6-ALPHA-OK. Beta must return M6-BETA-OK. Do not edit files. After both complete, reply with both values.",
        model: args.model,
        workspace,
        runtimeHome,
        gatewayBaseUrl: args.gatewayUrl,
        gatewayToken: args.gatewayToken,
        baseEnv: traceE2EClaudeEnv(),
        maxTurns: 10,
        timeoutMs: 180000,
      });
      if (run.exitCode !== 0) throw new Error(`Claude Code exited ${run.exitCode}`);
      bundle = collectTraceBundle({
        runtimeHome,
        traceDir: join(workspace, "trace"),
        runtimeKey: "claude-code",
        runtimeVersion,
        adapterVersion: "claude-code-1.0",
      });
      if (bundle.capture_status !== "complete") {
        throw new Error(`trace capture incomplete: ${bundle.incomplete_reasons.join(",")}`);
      }
    }
    const traceFiles = dedupeTraceObjects(bundle.files);
    const store = new LocalObjectStore(objectRoot);
    const score =
      args.terminal === "succeeded"
        ? {
            [item.case_key]: {
              resolved: true,
              tests_status: {
                FAIL_TO_PASS: { success: ["m6-trace-e2e"], failure: [] },
                PASS_TO_PASS: { success: [], failure: [] },
              },
            },
          }
        : { [item.case_key]: { resolved: false, error: "m6_trace_e2e_retryable" } };
    const scoreObject = store.putJson(score);
    const pendingObjects = [
      { artifact_kind: "score", ...scoreObject },
      ...traceFiles.map((file) => ({
        artifact_kind: file.artifact_kind,
        ...store.putBytes(file.bytes, { contentType: file.content_type }),
        logical_path: file.logical_path,
        native_format: file.native_format,
        runtime_version: file.runtime_version ?? bundle.runtime_version,
        format_version: file.format_version ?? "1",
      })),
    ];

    const uploadResponse = await fetch(
      `${callbackBase}/runner/v1/case-runs/${item.case_run_id}/upload-urls`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${tokenHeader}`,
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
      },
    );
    if (!uploadResponse.ok) throw new Error(`upload-urls HTTP ${uploadResponse.status}`);
    const { uploads } = await uploadResponse.json();
    for (const upload of uploads) {
      const pending = pendingObjects.find(
        (object) =>
          object.artifact_kind === upload.artifact_kind && object.sha256 === upload.sha256,
      );
      if (!pending) throw new Error(`unknown upload object ${upload.sha256}`);
      await putPresigned(upload, store.get(pending.object_key));
    }
    const reported = pendingObjects.map((pending) => {
      const upload = uploads.find(
        (candidate) =>
          candidate.artifact_kind === pending.artifact_kind &&
          candidate.sha256 === pending.sha256,
      );
      if (!upload) throw new Error(`upload URL missing ${pending.sha256}`);
      return {
        artifact_kind: pending.artifact_kind,
        object_key: upload.object_key,
        sha256: upload.sha256,
        size: upload.size,
        content_type: upload.content_type ?? pending.content_type,
        ...(pending.logical_path ? { logical_path: pending.logical_path } : {}),
        ...(pending.native_format ? { native_format: pending.native_format } : {}),
        ...(pending.runtime_version ? { runtime_version: pending.runtime_version } : {}),
        ...(pending.format_version ? { format_version: pending.format_version } : {}),
      };
    });
    const uploadPayload = requireValid("upload", {
      schema_version: "1.0",
      case_run_id: item.case_run_id,
      attempt: dispatch.attempt,
      objects: reported,
    });
    await callback(
      `${callbackBase}/runner/v1/case-runs/${item.case_run_id}/uploads`,
      uploadPayload,
      `evt_${item.case_run_id}_uploads_${dispatch.attempt}`,
      tokenHeader,
    );
    await callback(
      `${callbackBase}/runner/v1/case-runs/${item.case_run_id}/uploads`,
      uploadPayload,
      `evt_${item.case_run_id}_uploads_${dispatch.attempt}`,
      tokenHeader,
    );

    if (args.terminal === "failed") {
      const failure = requireValid("failure", {
        schema_version: "1.0",
        event_id: `evt_${item.case_run_id}_failed_${dispatch.attempt}`,
        case_run_id: item.case_run_id,
        attempt: dispatch.attempt,
        sequence: 2,
        error_code: "callback_retryable",
        retryable: true,
        error_summary: "Intentional M6 trace E2E retry",
        phase: "executing",
      });
      await callback(
        `${callbackBase}/runner/v1/case-runs/${item.case_run_id}/failed`,
        failure,
        failure.event_id,
        tokenHeader,
      );
      await callback(
        `${callbackBase}/runner/v1/case-runs/${item.case_run_id}/failed`,
        failure,
        failure.event_id,
        tokenHeader,
      );
    } else {
      const scoreReport = reported.find((object) => object.artifact_kind === "score");
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
        trace_manifest: traceManifestFromBundle(bundle, reported),
        summary: { duration_ms: 1000, input_tokens: 68, output_tokens: 58 },
      });
      await callback(
        `${callbackBase}/runner/v1/case-runs/${item.case_run_id}/completed`,
        completion,
        completion.event_id,
        tokenHeader,
      );
      await callback(
        `${callbackBase}/runner/v1/case-runs/${item.case_run_id}/completed`,
        completion,
        completion.event_id,
        tokenHeader,
      );
    }

    const evidence = {
      schema_version: "1.0",
      eval_run_id: dispatch.eval_run_id,
      case_run_id: item.case_run_id,
      attempt: dispatch.attempt,
      terminal: args.terminal,
      profile: args.profile,
      runtime_key: bundle.runtime_key,
      runtime_version: bundle.runtime_version,
      adapter_version: bundle.adapter_version,
      capture_status: bundle.capture_status,
      incomplete_reasons: bundle.incomplete_reasons,
      root_session_id:
        run.events.find((event) => event.type === "session_started")?.session_id ?? null,
      native_files: traceFiles.map((file) => ({
        logical_path: file.logical_path,
        artifact_kind: file.artifact_kind,
        native_format: file.native_format,
        sha256: file.sha256,
        size: file.size,
      })),
    };
    mkdirSync(resolve(args.evidence, ".."), { recursive: true });
    writeFileSync(resolve(args.evidence), `${JSON.stringify(evidence, null, 2)}\n`);
    console.log(JSON.stringify({ ok: true, ...evidence }));
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
  return 0;
}

main().then(
  (code) => process.exit(code ?? 0),
  (error) => {
    console.error(JSON.stringify({ ok: false, error: error.message, stack: error.stack }));
    process.exit(1);
  },
);

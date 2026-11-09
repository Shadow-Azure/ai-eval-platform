#!/usr/bin/env node
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

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
  return {
    bytes,
    tokenHeader: response.headers.get("x-runner-task-token"),
    tasksUrl: response.headers.get("x-runner-tasks-url"),
    modelToken: response.headers.get("x-runner-model-token"),
  };
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

function dockerRun(args, { allowFailure = false, timeout = 1_200_000 } = {}) {
  const result = spawnSync("docker", args, {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    timeout,
  });
  if (result.error) throw Object.assign(new Error(`docker 执行失败：${result.error.message}`), { code: "runtime_error" });
  if (result.status !== 0 && !allowFailure) {
    throw Object.assign(new Error(`docker 失败（${result.status}）：${(result.stderr || "").slice(-800)}`), {
      code: "runtime_error",
    });
  }
  return { status: result.status, stdout: result.stdout || "", stderr: result.stderr || "" };
}

function parsePytest(output) {
  const passed = new Set();
  const failed = new Set();
  for (const line of output.split("\n")) {
    const match = /^(PASSED|FAILED) (\S+)/.exec(line.trim());
    if (!match) continue;
    (match[1] === "PASSED" ? passed : failed).add(match[2]);
  }
  return { passed, failed };
}

/**
 * Execute one SWE-bench-style case: clone the repo, run Claude Code through
 * the gateway, capture the diff, apply the official test patch and run the
 * F2P/P2P tests inside the scoring image. Returns the official report payload
 * plus the trace bytes and terminal statuses.
 */
function realCase(dispatch, item, task, { modelToken, runnerRoot }) {
  const startedAt = Date.now();
  const workdir = mkdtempSync(join(tmpdir(), "eval-case-"));
  const testPatchPath = join(tmpdir(), `eval-test-${item.case_run_id}.patch`);
  try {
    if (!task.repo || !task.base_commit) throw new Error("真实用例缺少 repo/base_commit");
    if (!task.prompt) throw new Error("真实用例缺少 problem_statement");
    const environmentImage = task.environment_image || task.scoring_image_digest || task.execution_image_digest;
    if (!environmentImage) throw new Error("真实用例缺少 dataset environment image");
    if (!modelToken) throw new Error("manifest 未签发 model token");
    if (!task.model?.anthropic_base_url) throw new Error("tasks 未返回模型网关地址");

    // The SWE-bench scoring image already contains the repo checkout at the
    // instance base commit; copy it out instead of cloning from GitHub, which
    // is not reliably reachable from the fixed runner network.
    const source = dockerRun(["create", environmentImage]).stdout.trim();
    try {
      dockerRun(["cp", `${source}:/testbed/.`, workdir], { timeout: 300_000 });
    } finally {
      dockerRun(["rm", "-f", source], { allowFailure: true });
    }
    spawnSync("chmod", ["-R", "a+rwX", workdir], { encoding: "utf8", timeout: 120_000 });
    mkdirSync(join(workdir, ".runtime-home"), { recursive: true, mode: 0o777 });
    const checkout = spawnSync("git", ["-C", workdir, "checkout", task.base_commit], {
      encoding: "utf8",
      timeout: 120_000,
    });
    if (checkout.status !== 0) throw new Error(`checkout 失败：${(checkout.stderr || "").slice(-500)}`);

    const env = {
      WORKSPACE: "/testbed",
      RUNTIME_HOME: "/testbed/.runtime-home",
      CLAUDE_GATEWAY_BASE_URL: task.model.anthropic_base_url,
      CLAUDE_GATEWAY_TOKEN: modelToken,
      CLAUDE_MODEL: task.model.name,
      PROMPT: task.prompt,
      MAX_TURNS: process.env.REAL_TASK_MAX_TURNS || "30",
      TIMEOUT_MS: process.env.REAL_TASK_TIMEOUT_MS || "900000",
      EXECUTION_IMAGE_DIGEST: environmentImage,
    };
    const agentName = `eval-agent-${item.case_run_id.slice(0, 8)}-${process.pid}`;
    let agent;
    try {
      dockerRun([
        "run", "-d", "--name", agentName, "--network", "host",
        "-v", `${workdir}:/testbed`, "-v", `${runnerRoot}:/runner:ro`,
        environmentImage, "tail", "-f", "/dev/null",
      ]);
      // The dataset image is fixed; the generic agent runtime is installed
      // after start from Alibaba npm/PyPI sources. No per-agent image.
      dockerRun([
        "exec", agentName, "bash", "-lc",
        [
          "set -e",
          "if ! command -v node >/dev/null 2>&1; then",
          "  curl -fsSL https://registry.npmmirror.com/-/binary/node/v22.14.0/node-v22.14.0-linux-x64.tar.xz -o /tmp/node.tar.xz",
          "  mkdir -p /opt/node",
          "  tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1",
          "fi",
          "export PATH=/opt/miniconda3/envs/testbed/bin:/opt/node/bin:$PATH",
          "if ! command -v claude >/dev/null 2>&1; then",
          "  npm install -g @anthropic-ai/claude-code --registry=https://registry.npmmirror.com",
          "fi",
          "id eval >/dev/null 2>&1 || useradd -m -s /bin/bash eval",
          "chown -R eval:eval /testbed",
          "mkdir -p /home/eval && chown eval /home/eval",
        ].join("\n"),
      ], { timeout: 600_000 });
      const execArgs = ["exec", "-u", "eval", "-e", "HOME=/home/eval"];
      for (const [key, value] of Object.entries(env)) execArgs.push("-e", `${key}=${value}`);
      execArgs.push(
        agentName,
        "bash",
        "-lc",
        "export PATH=/opt/miniconda3/envs/testbed/bin:/opt/node/bin:$PATH; cd /testbed; node /runner/scripts/claude-code-smoke.mjs",
      );
      agent = dockerRun(execArgs, {
        allowFailure: true,
        timeout: Number(process.env.REAL_TASK_AGENT_TIMEOUT_MS || 1_000_000),
      });
    } catch (error) {
      agent = { status: 1, stdout: "", stderr: error.message };
    } finally {
      dockerRun(["rm", "-f", agentName], { allowFailure: true });
    }

    const tracePath = join(workdir, "trace", "claude-stream.jsonl");
    const trace = existsSync(tracePath) ? readFileSync(tracePath) : Buffer.from("");
    spawnSync("git", ["-C", workdir, "add", "-A", "--", ".", ":(exclude).runtime-home", ":(exclude)trace"], {
      encoding: "utf8",
      timeout: 60_000,
    });
    const diff = spawnSync(
      "git",
      ["-C", workdir, "diff", "--cached", "--", ".", ":(exclude).runtime-home", ":(exclude)trace"],
      { encoding: "utf8", timeout: 60_000 },
    );
    const patch = (diff.stdout || "").trim();
    if (agent.status !== 0) {
      const claudeStderrPath = join(workdir, "trace", "claude-stderr.log");
      const claudeStderr = existsSync(claudeStderrPath) ? readFileSync(claudeStderrPath, "utf8") : "";
      const failureDetail = `agent exit ${agent.status}\n${claudeStderr.slice(-6000)}\n${(agent.stderr || "").slice(-4000)}\n${(agent.stdout || "").slice(-2000)}`;
      console.error(failureDetail);
      return {
        score: {
          [task.case_key]: {
            resolved: false,
            error: "agent_failed",
            tests_status: { FAIL_TO_PASS: { success: [], failure: [] }, PASS_TO_PASS: { success: [], failure: [] } },
          },
        },
        trace: trace.length ? trace : Buffer.from(failureDetail, "utf8"),
        executionStatus: "failed",
        resultStatus: "not_scored",
        durationMs: Date.now() - startedAt,
      };
    }
    if (!patch) {
      return {
        score: {
          [task.case_key]: {
            resolved: false,
            error: "empty_patch",
            tests_status: { FAIL_TO_PASS: { success: [], failure: [] }, PASS_TO_PASS: { success: [], failure: [] } },
          },
        },
        trace,
        executionStatus: "succeeded",
        resultStatus: "unresolved",
        durationMs: Date.now() - startedAt,
      };
    }

    writeFileSync(testPatchPath, task.test_patch || "");
    dockerRun(
      [
        "run", "--rm", "-v", `${workdir}:/testbed`, "-v", `${testPatchPath}:/tmp/test.patch:ro`,
        environmentImage, "bash", "-lc", "cd /testbed && git apply --whitespace=nowarn /tmp/test.patch",
      ],
      { timeout: 120_000 },
    );
    const expected = task.expected_metrics || {};
    const failToPass = expected.fail_to_pass || [];
    const passToPass = expected.pass_to_pass || [];
    const testResult = dockerRun(
      [
        "run", "--rm", "-v", `${workdir}:/testbed`, environmentImage,
        "/opt/miniconda3/envs/testbed/bin/python", "-m", "pytest", "-q", "-rA",
        ...failToPass, ...passToPass,
      ],
      { allowFailure: true, timeout: 1_800_000 },
    );
    const { passed, failed } = parsePytest(`${testResult.stdout}\n${testResult.stderr}`);
    const success = (ids) => ids.filter((id) => passed.has(id) && !failed.has(id));
    const failure = (ids) => ids.filter((id) => !passed.has(id) || failed.has(id));
    const resolved =
      failToPass.length > 0 &&
      failToPass.every((id) => passed.has(id) && !failed.has(id)) &&
      passToPass.every((id) => passed.has(id) && !failed.has(id));
    const score = {
      [task.case_key]: {
        resolved,
        tests_status: {
          FAIL_TO_PASS: { success: success(failToPass), failure: failure(failToPass) },
          PASS_TO_PASS: { success: success(passToPass), failure: failure(passToPass) },
          FAIL_TO_FAIL: { success: [], failure: [] },
          PASS_TO_FAIL: { success: [], failure: [] },
        },
      },
    };
    return {
      score,
      trace,
      executionStatus: "succeeded",
      resultStatus: resolved ? "resolved" : "unresolved",
      durationMs: Date.now() - startedAt,
    };
  } finally {
    rmSync(workdir, { recursive: true, force: true });
    rmSync(testPatchPath, { force: true });
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.dispatch) {
    console.log("用法：node runner/scripts/real-task.mjs --dispatch <file> [--state-dir dir] [--object-store-root dir] [--token token] [--case caseRunId]");
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
  const { bytes: manifestBytes, tokenHeader, tasksUrl, modelToken } = await fetchManifest(dispatch.manifest_url).catch((error) =>
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

  const taskByCase = new Map();
  if (tasksUrl) {
    const tasksResponse = await fetch(tasksUrl, {
      headers: { authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(10000),
    }).catch(() => null);
    if (tasksResponse?.ok) {
      const taskPayload = await tasksResponse.json().catch(() => null);
      for (const task of taskPayload?.cases || []) {
        taskByCase.set(task.case_run_id, {
          ...task,
          model: taskPayload.model,
          execution_image_digest: taskPayload.execution_image_digest,
          scoring_image_digest: taskPayload.scoring_image_digest,
          environment_image: taskPayload.environment_image,
        });
      }
    }
  }
  const runnerRoot = resolve(process.env.RUNNER_ROOT || "runner");

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

      const task = taskByCase.get(item.case_run_id);
      const caseResult = task?.repo
        ? realCase(dispatch, item, task, { modelToken, runnerRoot })
        : { ...deterministicCase(dispatch, item), executionStatus: "succeeded", resultStatus: "resolved", durationMs: 0 };
      const { score, trace } = caseResult;
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
        execution_status: caseResult.executionStatus,
        result_status: caseResult.resultStatus,
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
        summary: { duration_ms: caseResult.durationMs, input_tokens: 0, output_tokens: 0 },
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

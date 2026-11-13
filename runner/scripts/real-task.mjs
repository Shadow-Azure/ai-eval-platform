#!/usr/bin/env node
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { materializeSnapshot } from "../lib/asset-bundle.mjs";
import { verifyToken } from "../lib/auth.mjs";
import { postCallback } from "../lib/callback-client.mjs";
import { requireValid, validateDispatch, validateManifest, validateStarted } from "../lib/contracts.mjs";
import { DispatchGuard } from "../lib/dispatch-guard.mjs";
import { ContractError } from "../lib/errors.mjs";
import { fetchManifest, sha256 } from "../lib/manifest-source.mjs";
import { LocalObjectStore } from "../lib/object-store.mjs";
import { getDatasetAdapter, getRuntimeAdapter } from "../lib/adapters.mjs";
import { composePrompt } from "../lib/prompt.mjs";
import { startHeartbeatSidecar, stopHeartbeatSidecar } from "./heartbeat-agent.mjs";
import { buildCaseTasks } from "../lib/task-payload.mjs";
import {
  collectTraceBundle,
  dedupeTraceObjects,
  traceManifestFromBundle,
  writeDiagnostic,
} from "../lib/trace-capture.mjs";

// The heartbeat sidecar owns monotonically increasing sequence numbers while
// the synchronous Docker call blocks this process. Terminal callbacks are sent
// only after the sidecar stops, so reserve high sequence numbers instead of
// colliding with heartbeat 2/3 on any run longer than one beat.
const TERMINAL_SEQUENCE = 1_000_000;
const TERMINAL_FAILURE_SEQUENCE = TERMINAL_SEQUENCE + 1;

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

/**
 * Per-case claims (M4-01) live on the job-local state dir. A retryable exit
 * must leave that dir reusable, so release the claim there; `callback_permanent`
 * keeps it so a replay cannot duplicate a rejected attempt, and the
 * `stale_attempt` exit (code 3) keeps today's behaviour.
 */
const CLAIM_KEEPING_CODES = new Set(["callback_permanent"]);
let releaseClaim = null;

function fail(message, code = "runtime_error", exitCode = 2) {
  if (releaseClaim && exitCode !== 0 && !CLAIM_KEEPING_CODES.has(code)) {
    try {
      releaseClaim();
    } catch (error) {
      console.error(`claim 释放失败：${error.message}`);
    }
    releaseClaim = null;
  }
  console.error(JSON.stringify({ status: "rejected", error_code: code, message }, null, 2));
  process.exit(exitCode);
}

const CAPACITY_WAIT_INITIAL_MS = 5_000;
const CAPACITY_WAIT_MAX_MS = 60_000;
const CAPACITY_WAIT_DEFAULT_SECONDS = 1_800;

/** Hard per-evaluation cap wait budget; 0 disables waiting (tests/fast fail). */
function capacityWaitSeconds(defaultSeconds = CAPACITY_WAIT_DEFAULT_SECONDS) {
  const raw = Number(process.env.RUNNER_CAPACITY_WAIT_SECONDS ?? defaultSeconds);
  return Number.isFinite(raw) && raw >= 0 ? raw : defaultSeconds;
}

/**
 * A 429 from `started` means the platform is at the evaluation's hard
 * concurrency cap (capacity_waiting). The attempt must stay queued, so retry
 * the same started event (same event_id) with bounded exponential backoff
 * (5s, 10s, ... capped at 60s) until RUNNER_CAPACITY_WAIT_SECONDS elapses.
 * Returns the last 429 result when the budget is exhausted.
 */
async function postStartedWithCapacityWait(url, payload, { eventId, token, waitSeconds }) {
  const deadline = Date.now() + waitSeconds * 1000;
  let delayMs = CAPACITY_WAIT_INITIAL_MS;
  for (;;) {
    // Hand 429 back to this loop so the first capacity retry waits 5s, not the
    // callback client's millisecond retry.
    const result = await postCallback(url, payload, { eventId, token, retryOn429: false });
    if (result.ok || result.stale || result.status !== 429) return result;
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) return result;
    await new Promise((resolve) => setTimeout(resolve, Math.min(delayMs, remainingMs)));
    delayMs = Math.min(delayMs * 2, CAPACITY_WAIT_MAX_MS);
  }
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

/**
 * M8-14: evaluation containers use host networking, so their GitHub egress is
 * the runner host's egress. When the platform sends a forward proxy in the
 * run-wide task payload, the asset install and the agent exec run behind it;
 * when the field is absent or empty, no `-e` flag is added and behaviour is
 * exactly the previous direct-network path.
 *
 * NO_PROXY keeps platform and mirror traffic off the proxy: the model
 * gateway/callback host, object storage (the asset bundle presign), loopback,
 * the npm mirror used by the runtime adapter and the default registry an
 * admin-authored install command may use. Hosts are derived from the payload
 * URLs; the remaining entries are the documented defaults.
 */
const DEFAULT_NO_PROXY = [
  "localhost",
  "127.0.0.1",
  "::1",
  "registry.npmmirror.com",
  "registry.npmjs.org",
];

function normalizeEgressProxy(value) {
  if (typeof value !== "string") return null;
  const proxy = value.trim();
  return proxy ? proxy : null;
}

function hostnameOf(value) {
  if (typeof value !== "string" || !value) return null;
  try {
    return new URL(value).hostname || null;
  } catch {
    return null;
  }
}

function egressNoProxy(task, callbackBaseUrl) {
  const hosts = new Set(DEFAULT_NO_PROXY);
  for (const candidate of [
    task?.model?.anthropic_base_url,
    task?.model?.openai_base_url,
    callbackBaseUrl,
    task?.asset_snapshot?.bundle_url,
  ]) {
    const host = hostnameOf(candidate);
    if (host) hosts.add(host);
  }
  return [...hosts].join(",");
}

function egressProxyEnvArgs(task, callbackBaseUrl) {
  const proxy = normalizeEgressProxy(task?.egress_proxy);
  if (!proxy) return [];
  const noProxy = egressNoProxy(task, callbackBaseUrl);
  return ["-e", `HTTPS_PROXY=${proxy}`, "-e", `HTTP_PROXY=${proxy}`, "-e", `NO_PROXY=${noProxy}`];
}

/**
 * M8-10: CLI-generated assets (OpenSpec/CodeSpec style) declare runtime
 * prerequisites — an install command for the CLI plus an init command that
 * generates the Skill/Command tree. The platform freezes the pair inside the
 * frozen asset snapshot as `runtime_setup` (an empty object when the snapshot
 * has none); older frozen snapshots may only carry `runtime_prerequisites`,
 * so accept that as a fallback. Both fields come from the admin-controlled
 * asset source, never from case data.
 *
 * M8-11: at eval time only the install command runs. `init` creates `.claude`
 * as the `eval` user, which conflicts with the host-side materialization of
 * the frozen snapshot (EACCES) and would compete for the same tree. The
 * frozen snapshot is the sole source of `.claude`; `init_command` in the
 * payload is deliberately ignored, even when present.
 *
 * The install command runs verbatim inside the agent container, so the runner
 * host never installs the CLI. An empty install means "no setup" (old
 * behaviour).
 */
const RUNTIME_SETUP_TIMEOUT_MS = 600_000;
const RUNTIME_SETUP_PATH = "/opt/miniconda3/envs/testbed/bin:/opt/node/bin";

function runtimeSetupFromSnapshot(assetSnapshot) {
  // `runtime_setup` is the frozen contract; fall back to the legacy field only
  // when the new one carries no command at all. `init_command` is ignored: it
  // is not part of the eval-time contract any more.
  for (const setup of [assetSnapshot?.runtime_setup, assetSnapshot?.runtime_prerequisites]) {
    if (!setup || typeof setup !== "object") continue;
    const install = typeof setup.install_command === "string" ? setup.install_command.trim() : "";
    if (!install) continue;
    return { install };
  }
  return null;
}

/**
 * Execute the install command in the running agent container with /testbed as
 * cwd. It runs as the workspace owner (`eval`) with the same HOME the load
 * test used, so a CLI installed under `$HOME` is found again by the agent.
 * The command is frozen, administrator-controlled text (never case data) and
 * runs verbatim; only a fixed PATH prefix is added so node/npm from the
 * runtime install are visible. Any non-zero exit or timeout raises
 * `asset_runtime_setup_failed` (phase `install`); callers must abort the case
 * instead of running the agent against an image without the CLI. M8-14 appends
 * the proxy env flags after the existing exec options; an unset proxy leaves
 * the argument list unchanged.
 */
function runAssetRuntimeSetup(agentName, { install }, egressArgs = []) {
  const script = `export PATH=${RUNTIME_SETUP_PATH}:$PATH\n${install}`;
  const args = [
    "exec", "-u", "eval", "-e", "HOME=/home/eval", "-w", "/testbed",
    ...egressArgs,
    agentName, "bash", "-lc", script,
  ];
  let result;
  try {
    result = dockerRun(args, { allowFailure: true, timeout: RUNTIME_SETUP_TIMEOUT_MS });
  } catch (error) {
    throw Object.assign(new Error(`资产运行时前置 install 执行失败：${error.message}`), {
      code: "asset_runtime_setup_failed",
      phase: "install",
    });
  }
  if (result.status !== 0) {
    const stderr = (result.stderr || "").slice(-4000);
    throw Object.assign(
      new Error(
        `资产运行时前置 install 退出码 ${result.status}：${(stderr || result.stdout || "").slice(-800)}`,
      ),
      { code: "asset_runtime_setup_failed", phase: "install", setupStderr: stderr },
    );
  }
}


/**
 * Execute one SWE-bench-style case: clone the repo, run Claude Code through
 * the gateway, capture the diff, apply the official test patch and run the
 * F2P/P2P tests inside the scoring image. Returns the official report payload
 * plus the trace bytes and terminal statuses.
 */
async function realCase(dispatch, item, task, { modelToken, runnerRoot, callbackBaseUrl }) {
  const datasetAdapter = getDatasetAdapter(task.adapter_key);
  const runtimeAdapter = getRuntimeAdapter(task.runtime_key);
  const egressArgs = egressProxyEnvArgs(task, callbackBaseUrl);
  const startedAt = Date.now();
  const workdir = mkdtempSync(join(tmpdir(), "eval-case-"));
  const testPatchPath = join(tmpdir(), `eval-test-${item.case_run_id}.patch`);
  try {
    if (!task.repo || !task.base_commit) throw new Error("真实用例缺少 repo/base_commit");
    if (!task.prompt) throw new Error("真实用例缺少 problem_statement");
    const environmentImage = datasetAdapter.environmentImage(task);
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
    mkdirSync(join(workdir, "trace"), { recursive: true });
    let runtimeVersion = runtimeAdapter.runtimeVersion ?? null;
    const collectCapture = (fallbackText = null) => {
      const capture = () =>
        collectTraceBundle({
          runtimeHome: join(workdir, ".runtime-home"),
          traceDir: join(workdir, "trace"),
          runtimeKey: "claude-code",
          runtimeVersion,
          adapterVersion: runtimeAdapter.adapterVersion ?? "claude-code-1.0",
        });
      let bundle = capture();
      if (bundle.files.length === 0) {
        writeDiagnostic(join(workdir, "trace"), "execution.log", fallbackText ?? "no trace output\n");
        bundle = capture();
      }
      return bundle;
    };
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
      PROMPT: runtimeAdapter.buildPrompt({
        problem: composePrompt(task.prompt, task.prompt_suffix),
        workspace: datasetAdapter.workspacePath(),
        constraints: datasetAdapter.constraints(task),
      }),
      MAX_TURNS: process.env.REAL_TASK_MAX_TURNS || "30",
      TIMEOUT_MS: process.env.REAL_TASK_TIMEOUT_MS || "900000",
      EXECUTION_IMAGE_DIGEST: environmentImage,
    };
    const agentName = `eval-agent-${item.case_run_id.slice(0, 8)}-${process.pid}`;
    let agent;
    let containerPatch = "";
    try {
      dockerRun([
        "run", "-d", "--name", agentName, "--network", "host",
        "-v", `${workdir}:/testbed`, "-v", `${runnerRoot}:/runner:ro`,
        environmentImage, "tail", "-f", "/dev/null",
      ]);
      // The dataset image is fixed; the generic agent runtime is installed
      // after start from Alibaba npm/PyPI sources. No per-agent image.
      dockerRun(["exec", agentName, "bash", "-lc", runtimeAdapter.installScript()], { timeout: 600_000 });
      // M8-11: run only the frozen asset install command (CLI availability)
      // inside the same container before the agent starts. `init_command` is
      // never executed at eval time: it would create `.claude` as `eval` and
      // collide with the frozen snapshot materialized below. A failure aborts
      // the case: the eval commands would otherwise hit an image without the
      // CLI.
      const runtimeSetup = runtimeSetupFromSnapshot(task.asset_snapshot);
      if (runtimeSetup) {
        try {
          runAssetRuntimeSetup(agentName, runtimeSetup, egressArgs);
        } catch (error) {
          const code = error.code || "asset_runtime_setup_failed";
          const message = `资产运行时前置失败：${error.message}`;
          console.error(message);
          writeDiagnostic(
            join(workdir, "trace"),
            "runtime-setup-failure.json",
            `${JSON.stringify({
              type: "trace.failed",
              code,
              phase: error.phase ?? null,
              message,
              stderr: error.setupStderr ?? "",
            })}\n`,
          );
          console.error(
            JSON.stringify({
              status: "runtime_setup_failed",
              error_code: code,
              phase: error.phase ?? null,
            }),
          );
          return {
            score: {
              [task.case_key]: {
                resolved: false,
                error: code,
                message,
                tests_status: {
                  FAIL_TO_PASS: { success: [], failure: [] },
                  PASS_TO_PASS: { success: [], failure: [] },
                },
              },
            },
            traceBundle: collectCapture(),
            executionStatus: "failed",
            resultStatus: "not_scored",
            durationMs: Date.now() - startedAt,
          };
        }
        console.error(
          JSON.stringify({
            status: "runtime_setup",
            snapshot_id: task.asset_snapshot?.snapshot_id,
            install: true,
            init: false,
          }),
        );
      }
      // M5-01: apply exactly the frozen snapshot to <workspace>/.claude AFTER
      // the runtime install runs. Eval time never runs `init`, so nothing in
      // the container competes for `.claude`; materializing last keeps the
      // frozen snapshot authoritative either way: the agent only ever loads
      // what the platform froze. A missing/invalid bundle fails the case
      // explicitly; it never silently falls back to repository/host assets.
      let assetsWritten = [];
      try {
        assetsWritten = await materializeSnapshot(task.asset_snapshot, workdir);
      } catch (error) {
        const message = `资产快照加载失败：${error.message}`;
        console.error(message);
        writeDiagnostic(
          join(workdir, "trace"),
          "asset-failure.json",
          `${JSON.stringify({ type: "trace.failed", code: error.code || "asset_invalid", message })}\n`,
        );
        return {
          score: {
            [task.case_key]: {
              resolved: false,
              error: "asset_invalid",
              message,
              tests_status: {
                FAIL_TO_PASS: { success: [], failure: [] },
                PASS_TO_PASS: { success: [], failure: [] },
              },
            },
          },
          traceBundle: collectCapture(),
          executionStatus: "failed",
          resultStatus: "not_scored",
          durationMs: Date.now() - startedAt,
        };
      }
      if (assetsWritten.length > 0) {
        // The host-side materializer writes as the runner user, after the
        // adapter install chowned the workspace to `eval`; hand the frozen
        // tree back so the non-root agent can traverse/load it.
        dockerRun(["exec", agentName, "chown", "-R", "eval:eval", "/testbed/.claude"], {
          allowFailure: true,
          timeout: 60_000,
        });
        console.error(
          JSON.stringify({
            status: "assets_materialized",
            snapshot_id: task.asset_snapshot?.snapshot_id,
            files: assetsWritten.length,
          }),
        );
      }
      const execArgs = ["exec", "-u", "eval", "-e", "HOME=/home/eval"];
      for (const [key, value] of Object.entries(env)) execArgs.push("-e", `${key}=${value}`);
      execArgs.push(...egressArgs);
      execArgs.push(
        agentName,
        "bash",
        "-lc",
        runtimeAdapter.agentScript(),
      );
      agent = dockerRun(execArgs, {
        allowFailure: true,
        timeout: Number(process.env.REAL_TASK_AGENT_TIMEOUT_MS || 1_000_000),
      });
      const version = dockerRun(
        ["exec", agentName, "bash", "-lc", "claude --version 2>/dev/null || true"],
        { allowFailure: true, timeout: 15_000 },
      );
      const versionMatch = /(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)/.exec(version.stdout || "");
      if (versionMatch) runtimeVersion = versionMatch[1];
      dockerRun(
        ["exec", "-u", "eval", agentName, "git", "-C", "/testbed", "add", "-A", "--", ".", ":(exclude).runtime-home", ":(exclude)trace", ":(exclude).claude"],
        { allowFailure: true, timeout: 60_000 },
      );
      containerPatch = dockerRun(
        ["exec", "-u", "eval", agentName, "git", "-C", "/testbed", "diff", "--cached", "--", ".", ":(exclude).runtime-home", ":(exclude)trace", ":(exclude).claude"],
        { allowFailure: true, timeout: 60_000 },
      ).stdout || "";
    } catch (error) {
      agent = { status: 1, stdout: "", stderr: error.message };
    } finally {
      // Claude Code may create session files with restrictive modes. The
      // scoring and trace-capture steps run on the host, so normalize access
      // while the container still exists and root can traverse every path.
      dockerRun(
        ["exec", agentName, "chmod", "-R", "a+rwX", "/testbed"],
        { allowFailure: true, timeout: 60_000 },
      );
      dockerRun(["rm", "-f", agentName], { allowFailure: true });
    }

    const tracePath = join(workdir, "trace", "claude-stream.jsonl");
    const traceBytes = existsSync(tracePath) ? readFileSync(tracePath) : Buffer.from("");
    // The workspace is chowned to the non-root agent user; host-side git
    // commands run as root and otherwise fail with "dubious ownership",
    // which silently produced empty patches even though the agent edited code.
    // Prefer the container-side capture: it runs as the workspace owner and
    // cannot hit host "dubious ownership" or index-permission failures.
    let patch = containerPatch.trim();
    if (!patch) {
      const safe = ["-c", `safe.directory=${workdir}`];
      spawnSync("git", [...safe, "-C", workdir, "add", "-A", "--", ".", ":(exclude).runtime-home", ":(exclude)trace", ":(exclude).claude"], {
        encoding: "utf8",
        timeout: 60_000,
      });
      const diff = spawnSync(
        "git",
        [...safe, "-C", workdir, "diff", "--cached", "--", ".", ":(exclude).runtime-home", ":(exclude)trace", ":(exclude).claude"],
        { encoding: "utf8", timeout: 60_000 },
      );
      patch = (diff.stdout || "").trim();
    }
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
        traceBundle: collectCapture(failureDetail),
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
        traceBundle: collectCapture(traceBytes.toString("utf8")),
        executionStatus: "succeeded",
        resultStatus: "unresolved",
        durationMs: Date.now() - startedAt,
      };
    }

    try {
      writeFileSync(testPatchPath, task.test_patch || "");
      dockerRun(datasetAdapter.applyTestPatchArgs(environmentImage, workdir, testPatchPath), { timeout: 120_000 });
      const testResult = dockerRun(
        datasetAdapter.testArgs(environmentImage, workdir, datasetAdapter.testIds(task)),
        { allowFailure: true, timeout: 1_800_000 },
      );
      const report = datasetAdapter.parseReport(task, `${testResult.stdout}\n${testResult.stderr}`);
      return {
        score: report.score,
        traceBundle: collectCapture(traceBytes.toString("utf8")),
        executionStatus: "succeeded",
        resultStatus: report.resultStatus,
        durationMs: Date.now() - startedAt,
      };
    } catch (error) {
      const message = error?.message || String(error);
      console.error(`评分阶段失败：${message}`);
      writeDiagnostic(
        join(workdir, "trace"),
        "scoring-failure.json",
        `${JSON.stringify({ error: message })}\n`,
      );
      return {
        score: {
          [task.case_key]: {
            resolved: false,
            error: "scoring_failed",
            message,
            tests_status: { FAIL_TO_PASS: { success: [], failure: [] }, PASS_TO_PASS: { success: [], failure: [] } },
          },
        },
        traceBundle: collectCapture(traceBytes.toString("utf8")),
        executionStatus: "failed",
        resultStatus: "not_scored",
        durationMs: Date.now() - startedAt,
      };
    }
  } finally {
    try {
      rmSync(workdir, { recursive: true, force: true });
    } catch (error) {
      console.error(`workdir cleanup failed: ${error.message}`);
    }
    try {
      rmSync(testPatchPath, { force: true });
    } catch (error) {
      console.error(`test patch cleanup failed: ${error.message}`);
    }
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

  let taskByCase = new Map();
  if (tasksUrl) {
    const tasksResponse = await fetch(tasksUrl, {
      headers: { authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(10000),
    }).catch(() => null);
    if (tasksResponse?.ok) {
      const taskPayload = await tasksResponse.json().catch(() => null);
      taskByCase = buildCaseTasks(taskPayload);
    }
  }
  const runnerRoot = resolve(process.env.RUNNER_ROOT || "runner");

  let cases = manifest.cases;
  if (args.caseRunId) {
    cases = cases.filter((item) => item.case_run_id === args.caseRunId);
    if (cases.length === 0) fail(`manifest 中不存在 case ${args.caseRunId}`, "manifest_invalid");
  }

  const guard = new DispatchGuard(resolve(args.stateDir));
  // One matrix job executes one case, so the claim key is per case; a whole
  // shard keeps the bare dispatch_key (backward compatible for non-matrix
  // callers and the synthetic flow). The platform stays the idempotency
  // authority across machines.
  const claimKey = args.caseRunId ? `${dispatch.dispatch_key}:${args.caseRunId}` : dispatch.dispatch_key;
  const claim = guard.claim(claimKey, {
    eval_run_id: dispatch.eval_run_id,
    shard_id: dispatch.shard_id,
    attempt: dispatch.attempt,
    ...(args.caseRunId ? { case_run_id: args.caseRunId } : {}),
  });
  if (!claim.claimed) {
    console.log(JSON.stringify({ status: "duplicate_dispatch", dispatch_key: dispatch.dispatch_key }));
    return 0;
  }
  releaseClaim = () => guard.release(claim.claimPath);
  // Wait at least one full case timeout (+5 min) so a case queued behind the
  // hard cap is not abandoned while the running cases are still legitimate.
  const capacityWait = capacityWaitSeconds((Number(manifest.case_timeout_seconds) || 3600) + 300);

  const store = new LocalObjectStore(resolve(args.objectRoot));
  const objects = [];

  const requestUploadUrls = async (item, pendingObjects) => {
    let lastError = null;
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      try {
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
          lastError = Object.assign(new Error(`upload-urls 换取失败：HTTP ${response.status}`), {
            code: "upload_failed",
            httpStatus: response.status,
          });
        } else {
          return payload.uploads;
        }
      } catch (error) {
        lastError = error;
      }
      if (attempt < 5) await new Promise((resolve) => setTimeout(resolve, 1000 * attempt));
    }
    throw lastError ?? new Error("upload-urls 换取失败");
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
      const startedResult = await postStartedWithCapacityWait(
        `${manifest.callback_base_url}/runner/v1/case-runs/${item.case_run_id}/started`,
        started,
        { eventId: started.event_id, token, waitSeconds: capacityWait },
      );
      if (startedResult.status === 429) {
        // Capacity budget exhausted: the attempt is still `queued` on the
        // platform. Never post a failed callback here; release the claim and
        // let a re-run pick the case up.
        fail(
          `评测并发已满：started 回调在 ${capacityWait}s 内持续 429，attempt 保持 queued`,
          "callback_retryable",
          1,
        );
      }
      if (startedResult.stale) {
        // Another Runner owns this attempt (cross-machine duplicate Job), or
        // the attempt is already terminal. Never start the agent.
        console.log(
          JSON.stringify({
            status: "stale_attempt",
            case_run_id: item.case_run_id,
            attempt: dispatch.attempt,
          }),
        );
        if (releaseClaim) {
          releaseClaim();
          releaseClaim = null;
        }
        return 0;
      }
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
      // M4-02: `realCase` blocks this process inside spawnSync("docker", …) for
      // the whole agent execution, so keep proving liveness from a sidecar
      // process. Only the real-repo path needs it; the deterministic synthetic
      // path stays exactly as before. The token travels via env only and a
      // sidecar failure must never change the case result.
      const heartbeatSidecar = task?.repo
        ? startHeartbeatSidecar({
            callbackBaseUrl: manifest.callback_base_url,
            token,
            caseRunId: item.case_run_id,
            attempt: dispatch.attempt,
            shardId: dispatch.shard_id,
            runnerRoot,
          })
        : null;
      let caseResult;
      try {
        caseResult = task?.repo
          ? await realCase(dispatch, item, task, {
              modelToken,
              runnerRoot,
              callbackBaseUrl: manifest.callback_base_url,
            })
          : { ...deterministicCase(dispatch, item), executionStatus: "succeeded", resultStatus: "resolved", durationMs: 0 };
      } finally {
        // Stopped on both success and exception: SIGTERM, up to 5s, then SIGKILL.
        try {
          await stopHeartbeatSidecar(heartbeatSidecar);
        } catch {
          // Best-effort liveness only.
        }
      }
      const { score } = caseResult;
      const caseBundle = caseResult.traceBundle ?? {
        schema_version: "1.0",
        runtime_key: "synthetic",
        runtime_version: "1.0",
        adapter_version: "synthetic-1.0",
        capture_status: "partial",
        incomplete_reasons: ["synthetic_trace"],
        files: [
          {
            logical_path: "synthetic/case.jsonl",
            artifact_kind: "trace",
            content_type: "application/x-ndjson",
            native_format: "synthetic.case+jsonl",
            format_version: "1",
            runtime_version: "1.0",
            bytes: Buffer.from(caseResult.trace, "utf8"),
          },
        ],
      };
      const traceFiles = dedupeTraceObjects(caseBundle.files);
      const uploadFields = (file) => ({
        logical_path: file.logical_path,
        native_format: file.native_format,
        runtime_version: file.runtime_version ?? caseBundle.runtime_version ?? "unknown",
        format_version: file.format_version ?? "1",
      });
      const scoreObject = store.putJson(score);
      const pendingObjects = [
        { artifact_kind: "score", ...scoreObject },
        ...traceFiles.map((file) => ({
          artifact_kind: file.artifact_kind,
          ...store.putBytes(file.bytes, { contentType: file.content_type }),
          ...uploadFields(file),
        })),
      ];

      let reportedObjects = pendingObjects;
      if (presignUploads) {
        const uploads = await requestUploadUrls(item, pendingObjects);
        const keyOf = (kind, digest) => `${kind}\0${digest}`;
        const pendingByKey = new Map(
          pendingObjects.map((pending) => [keyOf(pending.artifact_kind, pending.sha256), pending]),
        );
        for (const upload of uploads) {
          const pending = pendingByKey.get(keyOf(upload.artifact_kind, upload.sha256));
          if (!pending) {
            throw Object.assign(
              new Error(`upload-urls 返回未知对象 ${upload.artifact_kind}/${upload.sha256}`),
              { code: "upload_failed" },
            );
          }
          const bytes = store.get(pending.object_key);
          let putError = null;
          for (let attempt = 1; attempt <= 5; attempt += 1) {
            try {
              const putResponse = await fetch(upload.upload_url, {
                method: "PUT",
                headers: upload.headers ?? {},
                body: bytes,
                signal: AbortSignal.timeout(30000),
              });
              if (putResponse.ok) {
                putError = null;
                break;
              }
              putError = Object.assign(new Error(`对象上传失败：HTTP ${putResponse.status}`), {
                code: "upload_failed",
              });
            } catch (error) {
              putError = error;
            }
            if (attempt < 5) await new Promise((resolve) => setTimeout(resolve, 1000 * attempt));
          }
          if (putError) throw putError;
        }
        reportedObjects = pendingObjects.map((pending) => {
          const upload = uploads.find(
            (candidate) =>
              candidate.artifact_kind === pending.artifact_kind && candidate.sha256 === pending.sha256,
          );
          if (!upload) {
            throw Object.assign(
              new Error(`upload-urls 缺少对象 ${pending.artifact_kind}/${pending.sha256}`),
              { code: "upload_failed" },
            );
          }
          return {
            ...pending,
            object_key: upload.object_key,
            sha256: upload.sha256,
            size: upload.size,
            content_type: upload.headers?.["content-type"] ?? pending.content_type,
          };
        });
      }
      objects.push(...reportedObjects);

      const uploadObject = ({ artifact_kind, object_key, sha256: digest, size, content_type, logical_path, native_format, runtime_version, format_version }) => ({
        artifact_kind,
        object_key,
        sha256: digest,
        size,
        content_type,
        ...(logical_path ? { logical_path } : {}),
        ...(native_format ? { native_format } : {}),
        ...(runtime_version ? { runtime_version } : {}),
        ...(format_version ? { format_version } : {}),
      });
      const upload = requireValid("upload", {
        schema_version: "1.0",
        case_run_id: item.case_run_id,
        attempt: dispatch.attempt,
        objects: reportedObjects.map(uploadObject),
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
          sequence: TERMINAL_FAILURE_SEQUENCE,
          error_code: retryable ? "callback_retryable" : "upload_failed",
          retryable,
          error_summary: `上传清单回调失败：HTTP ${uploadResult.status}`,
          phase: "uploading",
        }), { eventId: `evt_${item.case_run_id}_failed_${dispatch.attempt}`, token });
        fail(`上传清单回调失败：HTTP ${uploadResult.status}`, retryable ? "callback_retryable" : "upload_failed", 1);
      }

      const scoreReport = reportedObjects.find((object) => object.artifact_kind === "score");
      const traceManifest = traceManifestFromBundle(caseBundle, reportedObjects);
      const completion = requireValid("completion", {
        schema_version: "1.0",
        event_id: `evt_${dispatch.shard_id}_${item.case_run_id}_completed`,
        case_run_id: item.case_run_id,
        attempt: dispatch.attempt,
        sequence: TERMINAL_SEQUENCE,
        execution_status: caseResult.executionStatus,
        result_status: caseResult.resultStatus,
        score_manifest: {
          object_key: scoreReport.object_key,
          sha256: scoreReport.sha256,
          size: scoreReport.size,
        },
        ...(traceManifest.objects.length > 0 ? { trace_manifest: traceManifest } : {}),
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
          sequence: TERMINAL_FAILURE_SEQUENCE,
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
        sequence: TERMINAL_FAILURE_SEQUENCE,
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

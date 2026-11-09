#!/usr/bin/env node
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { postCallback } from "../lib/callback-client.mjs";

/**
 * M4-02 (#79) heartbeat sidecar.
 *
 * `real-task.mjs` runs the agent inside a synchronous `spawnSync("docker", …)`
 * for up to the case timeout, so its event loop is blocked for the whole case.
 * The platform reaps attempts whose heartbeat is older than the heartbeat
 * timeout (default 300s); one heartbeat before the agent starts is therefore
 * not enough for a healthy long case.
 *
 * This script is spawned as a separate process before the agent runs and POSTs
 * a progress heartbeat every interval until the control script stops it. It
 * holds no state: sequence starts at 2 because `real-task` already sends
 * sequence 1 synchronously.
 *
 * Secret hygiene: the task token is read from the environment only (never
 * argv), and nothing from the payload/token is ever printed. stderr carries a
 * single compact start/stop status line.
 */

const DEFAULT_INTERVAL_MS = 30_000;
const MIN_INTERVAL_MS = 1_000;
/** 409 is the platform's stale/terminal signal; 404/410 mean the case is gone. */
const TERMINAL_STATUSES = new Set([404, 409, 410]);
const SENTINEL_STOP = Symbol("stopped");

function logLine(message) {
  process.stderr.write(`heartbeat: ${message}\n`);
}

function parseIntervalMs(raw) {
  if (raw === undefined || raw === null || raw === "") return DEFAULT_INTERVAL_MS;
  const value = Number(raw);
  if (!Number.isFinite(value)) return DEFAULT_INTERVAL_MS;
  return Math.max(MIN_INTERVAL_MS, Math.floor(value));
}

export function buildHeartbeatPayload({ caseRunId, attempt, sequence, shardId, now = new Date() }) {
  // No runner_id here: the heartbeat schema is additionalProperties:false and
  // runner binding is proven by the started callback / X-Runner-Id header, not
  // by this event.
  return {
    schema_version: "1.0",
    event_id: `evt_${shardId || "case"}_${caseRunId}_heartbeat_${sequence}`,
    case_run_id: caseRunId,
    attempt: Number(attempt),
    sequence,
    phase: "executing",
    progress: { completed: 0, total: 1 },
    occurred_at: now.toISOString(),
  };
}

/** Read and validate the sidecar environment; `error` names the bad variable. */
export function readConfig(env = process.env, argv = []) {
  const attempt = Number(env.ATTEMPT);
  const config = {
    callbackBaseUrl: String(env.CALLBACK_BASE_URL || "").replace(/\/+$/, ""),
    token: env.RUNNER_TASK_TOKEN || "",
    caseRunId: env.CASE_RUN_ID || "",
    attempt,
    shardId: env.SHARD_ID || "",
    intervalMs: parseIntervalMs(env.RUNNER_HEARTBEAT_INTERVAL_MS),
    once: argv.includes("--once") || env.RUNNER_HEARTBEAT_ONCE === "1",
  };
  if (!config.callbackBaseUrl) config.error = "CALLBACK_BASE_URL 未设置";
  else if (!config.token) config.error = "RUNNER_TASK_TOKEN 未设置";
  else if (!config.caseRunId) config.error = "CASE_RUN_ID 未设置";
  else if (!Number.isInteger(attempt) || attempt < 1) config.error = "ATTEMPT 必须是 >= 1 的整数";
  return config;
}

function heartbeatUrl(config) {
  return `${config.callbackBaseUrl}/runner/v1/case-runs/${encodeURIComponent(config.caseRunId)}/heartbeat`;
}

/** One heartbeat. Returns { ok, status, terminal }. Never throws, never logs. */
export async function sendHeartbeat(config, sequence) {
  const payload = buildHeartbeatPayload({
    caseRunId: config.caseRunId,
    attempt: config.attempt,
    sequence,
    shardId: config.shardId,
  });
  const result = await postCallback(heartbeatUrl(config), payload, {
    eventId: payload.event_id,
    token: config.token,
  });
  return {
    ok: Boolean(result.ok),
    status: result.status,
    stale: Boolean(result.stale),
    terminal: Boolean(result.stale) || TERMINAL_STATUSES.has(result.status),
  };
}

/** Sleep that wakes up as soon as `signal` aborts (so SIGTERM never waits a full interval). */
function sleep(ms, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    let timer = null;
    const onAbort = () => {
      if (timer) clearTimeout(timer);
      resolve();
    };
    timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/** In-flight POST loses to a signal so the child exits promptly. */
function raceAbort(promise, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve(SENTINEL_STOP);
      return;
    }
    let settled = false;
    const onAbort = () => {
      if (settled) return;
      settled = true;
      resolve(SENTINEL_STOP);
    };
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      () => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", onAbort);
        resolve({ ok: false, status: 0, terminal: false });
      },
    );
  });
}

async function runLoop(config, signal) {
  let sequence = 2;
  let sent = 0;
  let failed = 0;
  for (;;) {
    if (signal.aborted) return { reason: "stopped", sent, failed, sequence };
    const result = await raceAbort(sendHeartbeat(config, sequence), signal);
    if (result === SENTINEL_STOP) return { reason: "stopped", sent, failed, sequence };
    if (result.terminal) {
      // The attempt is terminal or stale: no more heartbeats can help.
      return { reason: "terminal", sent, failed, sequence };
    }
    if (result.ok) sent += 1;
    else failed += 1;
    if (config.once) return { reason: "once", sent, failed, sequence };
    sequence += 1;
    if (signal.aborted) return { reason: "stopped", sent, failed, sequence };
    await sleep(config.intervalMs, signal);
  }
}

export async function main({ env = process.env, argv = process.argv.slice(2) } = {}) {
  const config = readConfig(env, argv);
  if (config.error) {
    logLine(config.error);
    return 2;
  }

  const controller = new AbortController();
  const stop = () => controller.abort();
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);

  logLine(
    `start case=${config.caseRunId} attempt=${config.attempt} interval_ms=${config.intervalMs}${config.once ? " once" : ""}`,
  );
  const outcome = await runLoop(config, controller.signal);
  logLine(`stop reason=${outcome.reason} sent=${outcome.sent} failed=${outcome.failed}`);

  process.off("SIGTERM", stop);
  process.off("SIGINT", stop);
  return 0;
}

// Best-effort orphan guard: if the control script exits without stopping us
// (e.g. a crash), take the sidecar down with it instead of beating forever.
let activeChild = null;
process.once("exit", () => {
  if (!activeChild) return;
  try {
    activeChild.kill("SIGKILL");
  } catch {
    // already gone
  }
});

/**
 * Spawn this same script as the heartbeat sidecar for one real case execution.
 * Returns null when disabled (`RUNNER_HEARTBEAT_DISABLE=1`) or when the script
 * is missing: a sidecar problem must never change the case result.
 */
export function startHeartbeatSidecar({
  callbackBaseUrl,
  token,
  caseRunId,
  attempt,
  shardId,
  runnerRoot,
  env = process.env,
}) {
  if (env.RUNNER_HEARTBEAT_DISABLE === "1") return null;
  const script = join(runnerRoot, "scripts", "heartbeat-agent.mjs");
  if (!existsSync(script)) return null;
  try {
    const child = spawn(process.execPath, [script], {
      stdio: ["ignore", "ignore", "pipe"],
      env: {
        ...env,
        CALLBACK_BASE_URL: callbackBaseUrl,
        RUNNER_TASK_TOKEN: token,
        CASE_RUN_ID: caseRunId,
        ATTEMPT: String(attempt),
        ...(shardId ? { SHARD_ID: shardId } : {}),
      },
    });
    child.on("error", () => {});
    // Drain stderr: the sidecar only prints two compact lines, but an unconsumed
    // pipe would block it once the buffer fills.
    child.stderr?.resume();
    activeChild = child;
    return child;
  } catch {
    return null;
  }
}

/**
 * SIGTERM, wait up to `timeoutMs` for the child to exit, then SIGKILL.
 * Idempotent and safe on null/already-exited children.
 */
export async function stopHeartbeatSidecar(child, { timeoutMs = 5_000 } = {}) {
  if (!child) return;
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(killTimer);
      if (activeChild === child) activeChild = null;
      resolve();
    };
    const killTimer = setTimeout(() => {
      try {
        child.kill("SIGKILL");
      } catch {
        // already gone
      }
      finish();
    }, timeoutMs);
    child.once("exit", finish);
    try {
      child.kill("SIGTERM");
    } catch {
      finish();
    }
    // The child may have exited between the guard above and the listener
    // registration; finish immediately instead of waiting for the SIGKILL.
    if (child.exitCode !== null || child.signalCode !== null) finish();
  });
}

const entry = process.argv[1];
if (entry && import.meta.url === pathToFileURL(entry).href) {
  main().then(
    (code) => process.exit(code),
    (error) => {
      logLine(`fatal ${error?.message ?? error}`);
      process.exit(1);
    },
  );
}

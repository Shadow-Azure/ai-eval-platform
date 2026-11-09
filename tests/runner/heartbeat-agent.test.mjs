import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { join } from "node:path";
import test from "node:test";

import { validateHeartbeat } from "../../runner/lib/contracts.mjs";
import {
  startHeartbeatSidecar,
  stopHeartbeatSidecar,
} from "../../runner/scripts/heartbeat-agent.mjs";
import { repoRoot } from "./scenario-helpers.mjs";

/**
 * M4-02 (#79): the real-task control script blocks inside spawnSync("docker")
 * for the whole case, so a single pre-run heartbeat is not enough: the
 * platform's heartbeat timeout (default 300s) would reap a healthy long case.
 * The sidecar keeps beating from a second process until the control script
 * stops it. These tests pin the sidecar's wire contract, its shutdown
 * behaviour and the fact that it never leaks the task token.
 */

const agentPath = join(repoRoot, "runner", "scripts", "heartbeat-agent.mjs");
const runnerRoot = join(repoRoot, "runner");
const TOKEN = "hb-secret-token-value";

function runAgent({ args = [], env = {} } = {}) {
  const child = spawn(process.execPath, [agentPath, ...args], {
    cwd: repoRoot,
    env: { ...process.env, ...env },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const exited = new Promise((resolve) => {
    child.on("close", (code, signal) => resolve({ code, signal }));
  });
  return {
    child,
    exited,
    get stdout() { return stdout; },
    get stderr() { return stderr; },
  };
}

/** Run the sidecar to completion (one-shot modes) and return its exit result. */
async function runAgentOnce(options) {
  const agent = runAgent(options);
  const { code, signal } = await agent.exited;
  return { code, signal, stdout: agent.stdout, stderr: agent.stderr, child: agent.child };
}

/** Never leave a looping sidecar behind: it would keep the test process alive. */
async function stopAgent(agent) {
  if (agent.child.exitCode === null && agent.child.signalCode === null) {
    try {
      agent.child.kill("SIGTERM");
    } catch {
      // already gone
    }
  }
  await agent.exited;
}

function startStub(handler = () => ({ status: 200, body: { ok: true } })) {
  return new Promise((resolve) => {
    const requests = [];
    const server = createServer(async (request, response) => {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const raw = Buffer.concat(chunks).toString("utf8");
      let body = null;
      try {
        body = raw ? JSON.parse(raw) : null;
      } catch {
        body = raw;
      }
      const record = { method: request.method, url: request.url, headers: request.headers, body, status: 0 };
      requests.push(record);
      const reply = handler(record, requests.length) ?? { status: 200, body: { ok: true } };
      record.status = reply.status;
      response.writeHead(reply.status, { "content-type": "application/json" });
      response.end(JSON.stringify(reply.body ?? {}));
    });
    server.listen(0, "127.0.0.1", () => {
      resolve({
        requests,
        get accepted() { return requests.filter((item) => item.status === 200).length; },
        baseUrl: `http://127.0.0.1:${server.address().port}`,
        close: () => new Promise((res, rej) => {
          // Undici keeps the keep-alive socket open; close it or this hangs.
          server.closeAllConnections?.();
          server.close((error) => (error ? rej(error) : res()));
        }),
      });
    });
  });
}

async function waitFor(predicate, { timeoutMs = 8000, intervalMs = 15 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error("timed out waiting for condition");
}

const baseEnv = (stub, overrides = {}) => ({
  CALLBACK_BASE_URL: stub.baseUrl,
  RUNNER_TASK_TOKEN: TOKEN,
  CASE_RUN_ID: "ecr_hb",
  ATTEMPT: "1",
  SHARD_ID: "shard_test",
  RUNNER_ID: "",
  RUNNER_HEARTBEAT_ONCE: "",
  RUNNER_HEARTBEAT_INTERVAL_MS: "",
  RUNNER_HEARTBEAT_DISABLE: "",
  ...overrides,
});

const heartbeatPath = (caseRunId = "ecr_hb") => `/runner/v1/case-runs/${caseRunId}/heartbeat`;

const sequences = (requests) => requests.map((request) => request.body.sequence);

test("--once posts exactly one heartbeat with sequence 2 and exits 0", async () => {
  const stub = await startStub();
  try {
    const result = await runAgentOnce({ args: ["--once"], env: baseEnv(stub) });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stdout, "", "the sidecar must not write to stdout");

    assert.equal(stub.requests.length, 1);
    const [record] = stub.requests;
    assert.equal(record.method, "POST");
    assert.equal(record.url, heartbeatPath());
    assert.equal(record.headers.authorization, `Bearer ${TOKEN}`);
    assert.equal(record.headers["idempotency-key"], "evt_shard_test_ecr_hb_heartbeat_2");
    assert.deepEqual(record.body, {
      schema_version: "1.0",
      event_id: "evt_shard_test_ecr_hb_heartbeat_2",
      case_run_id: "ecr_hb",
      attempt: 1,
      sequence: 2,
      phase: "executing",
      progress: { completed: 0, total: 1 },
      occurred_at: record.body.occurred_at,
    });
    assert.ok(!Number.isNaN(Date.parse(record.body.occurred_at)), "occurred_at must be an ISO timestamp");
    // The payload must be valid for the heartbeat schema.
    assert.deepEqual(validateHeartbeat(record.body), { ok: true, errors: [], kind: "heartbeat" });
  } finally {
    await stub.close();
  }
});

test("the event id falls back to the case prefix when SHARD_ID is absent", async () => {
  const stub = await startStub();
  try {
    const result = await runAgentOnce({ args: ["--once"], env: baseEnv(stub, { SHARD_ID: "" }) });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(stub.requests[0].body.event_id, "evt_case_ecr_hb_heartbeat_2");
  } finally {
    await stub.close();
  }
});

test("RUNNER_ID never leaks into the heartbeat payload", async () => {
  // The heartbeat schema is additionalProperties:false and has no runner_id;
  // runner binding belongs to the started callback / X-Runner-Id header. A job
  // env that sets RUNNER_ID must still produce a contract-valid heartbeat.
  const stub = await startStub();
  try {
    const result = await runAgentOnce({
      args: ["--once"],
      env: baseEnv(stub, { RUNNER_ID: "fixed-linux-eval" }),
    });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(Object.hasOwn(stub.requests[0].body, "runner_id"), false);
    assert.deepEqual(validateHeartbeat(stub.requests[0].body), { ok: true, errors: [], kind: "heartbeat" });
  } finally {
    await stub.close();
  }
});

test("RUNNER_HEARTBEAT_ONCE=1 behaves like --once", async () => {
  const stub = await startStub();
  try {
    const result = await runAgentOnce({ env: baseEnv(stub, { RUNNER_HEARTBEAT_ONCE: "1" }) });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(stub.requests.length, 1);
    assert.equal(stub.requests[0].body.sequence, 2);
  } finally {
    await stub.close();
  }
});

test("loop mode sends strictly increasing sequences and SIGTERM stops it with exit 0", async () => {
  const stub = await startStub();
  // The configured floor is 1s (see RUNNER_HEARTBEAT_INTERVAL_MS clamp), so 20
  // is clamped up; the test only needs >=3 beats, not a 20ms cadence.
  const agent = runAgent({ env: baseEnv(stub, { RUNNER_HEARTBEAT_INTERVAL_MS: "20" }) });
  try {
    await waitFor(() => stub.requests.length >= 3);
    const seen = sequences(stub.requests);
    assert.deepEqual(seen.slice(0, 3), [2, 3, 4]);
    assert.ok(seen.every((value, index) => index === 0 || value > seen[index - 1]), "sequences must increase");
    assert.equal(agent.child.exitCode, null, "the loop must still be running");

    const stoppedAt = Date.now();
    agent.child.kill("SIGTERM");
    const { code } = await agent.exited;
    assert.equal(code, 0);
    assert.ok(Date.now() - stoppedAt < 3000, "SIGTERM must stop the loop promptly");
    assert.equal(agent.stdout, "");
  } finally {
    await stopAgent(agent);
    await stub.close();
  }
});

test("the token never appears on stdout or stderr", async () => {
  const stub = await startStub();
  const agent = runAgent({ env: baseEnv(stub) });
  try {
    await waitFor(() => stub.requests.length >= 1);
    agent.child.kill("SIGTERM");
    const { code } = await agent.exited;
    assert.equal(code, 0);
    assert.equal(agent.stdout, "");
    assert.ok(!agent.stderr.includes(TOKEN), "stderr must not contain the token");
    assert.ok(!agent.stderr.includes("schema_version"), "stderr must not dump the payload");
    assert.ok(!agent.stderr.includes("event_id"), "stderr must not dump the payload");
  } finally {
    await stopAgent(agent);
    await stub.close();
  }
});

test("a 500 response does not kill the loop", async () => {
  // The first postCallback exhausts its internal retries (4x 500); the loop
  // must keep beating afterwards.
  const stub = await startStub((_record, index) =>
    index <= 4
      ? { status: 500, body: { error: { code: "temporary_failure" } } }
      : { status: 200, body: { ok: true } }
  );
  const agent = runAgent({ env: baseEnv(stub, { RUNNER_HEARTBEAT_INTERVAL_MS: "20" }) });
  try {
    await waitFor(() => stub.accepted >= 2);
    assert.equal(agent.child.exitCode, null, "a 5xx must not terminate the sidecar");
    const accepted = stub.requests.filter((request) => request.status === 200);
    assert.deepEqual(sequences(accepted).slice(0, 2), [3, 4]);

    agent.child.kill("SIGTERM");
    const { code } = await agent.exited;
    assert.equal(code, 0);
  } finally {
    await stopAgent(agent);
    await stub.close();
  }
});

test("a 409 terminal response ends the loop with exit 0", async () => {
  const stub = await startStub(() => ({ status: 409, body: { error: { code: "stale_attempt" } } }));
  try {
    const result = await runAgentOnce({ env: baseEnv(stub, { RUNNER_HEARTBEAT_INTERVAL_MS: "20" }) });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(stub.requests.length, 1, "a stale/terminal attempt must not be retried");
  } finally {
    await stub.close();
  }
});

test("missing required env fails fast without echoing secrets", async () => {
  const stub = await startStub();
  try {
    const result = await runAgentOnce({ args: ["--once"], env: baseEnv(stub, { RUNNER_TASK_TOKEN: "" }) });
    assert.equal(result.code, 2);
    assert.match(result.stderr, /RUNNER_TASK_TOKEN/);
    assert.equal(result.stdout, "");
    assert.equal(stub.requests.length, 0);
  } finally {
    await stub.close();
  }
});

test("startHeartbeatSidecar keeps the token out of argv and stopHeartbeatSidecar ends it", async () => {
  const stub = await startStub();
  const env = baseEnv(stub);
  // Keep the default 30s cadence: stopping must not wait for the timer.
  const child = startHeartbeatSidecar({
    callbackBaseUrl: stub.baseUrl,
    token: TOKEN,
    caseRunId: "ecr_hb",
    attempt: 1,
    shardId: "shard_test",
    runnerRoot,
    env,
  });
  try {
    assert.ok(child, "the sidecar must be spawned");
    assert.equal(child.spawnargs.join(" ").includes(TOKEN), false, "the token must travel via env only");
    assert.equal(child.spawnargs.join(" ").includes("--token"), false);

    await waitFor(() => stub.requests.length >= 1);
    assert.equal(stub.requests[0].body.sequence, 2);
    assert.equal(stub.requests[0].headers.authorization, `Bearer ${TOKEN}`);

    const stoppedAt = Date.now();
    await stopHeartbeatSidecar(child);
    assert.equal(child.exitCode, 0);
    assert.ok(Date.now() - stoppedAt < 3000, "the sidecar must exit without waiting for the interval");

    // Idempotent: a second stop (and a null child) resolve immediately.
    await stopHeartbeatSidecar(child);
    await stopHeartbeatSidecar(null);
  } finally {
    await stopHeartbeatSidecar(child);
    await stub.close();
  }
});

test("RUNNER_HEARTBEAT_DISABLE=1 and a missing script skip the sidecar", () => {
  const base = {
    callbackBaseUrl: "http://127.0.0.1:1",
    token: TOKEN,
    caseRunId: "ecr_hb",
    attempt: 1,
    shardId: "shard_test",
    runnerRoot,
  };
  assert.equal(startHeartbeatSidecar({ ...base, env: { ...process.env, RUNNER_HEARTBEAT_DISABLE: "1" } }), null);
  assert.equal(
    startHeartbeatSidecar({ ...base, env: { ...process.env, RUNNER_HEARTBEAT_DISABLE: "" }, runnerRoot: join(repoRoot, "missing-runner-root") }),
    null,
  );
});

test("stopHeartbeatSidecar SIGKILLs a sidecar that ignores SIGTERM", async () => {
  // A stuck child must not hold the case open: the control script only waits
  // 5s before escalating, and this exercises that escalation.
  const child = spawn(
    process.execPath,
    ["-e", "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);"],
    { stdio: ["ignore", "ignore", "pipe"] },
  );
  child.stderr?.resume();
  const exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
  // Give the child time to install its SIGTERM handler.
  await new Promise((resolve) => setTimeout(resolve, 200));

  const stoppedAt = Date.now();
  await stopHeartbeatSidecar(child, { timeoutMs: 300 });
  const { signal } = await exited;
  assert.ok(Date.now() - stoppedAt >= 250, "must wait for the exit grace period before SIGKILL");
  assert.equal(signal, "SIGKILL");
});

import assert from "node:assert/strict";
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { createScenario, repoRoot, runNode, taskSecret } from "./scenario-helpers.mjs";

const script = join(repoRoot, "runner", "scripts", "real-task.mjs");

const caseArgs = (ctx, caseRunId) => [
  "--dispatch", ctx.dispatchPath,
  "--case", caseRunId,
  "--state-dir", ctx.stateDir,
  "--object-store-root", ctx.objectRoot,
];

const runCase = (ctx, caseRunId, env = {}) =>
  runNode(script, caseArgs(ctx, caseRunId), {
    RUNNER_TASK_TOKEN: ctx.token,
    RUNNER_TASK_SECRET: taskSecret,
    ...env,
  });

const requestsFor = (ctx, caseRunId, action) =>
  ctx.platform.requests.filter((request) => request.path === `/runner/v1/case-runs/${caseRunId}/${action}`);

const postRequests = (ctx) => ctx.platform.requests.filter((request) => request.method !== "GET");

const failureCallbackCount = (ctx) =>
  ctx.platform.requests.filter((request) => request.path.endsWith("/failed")).length;

/** Local claims still present for this state dir (empty after a release). */
const claims = (ctx) => {
  const dir = join(ctx.stateDir, "dispatch-claims");
  return existsSync(dir) ? readdirSync(dir) : [];
};

const statusOf = (result) => JSON.parse(result.stdout.trim().split("\n").at(-1)).status;

test("--case executes exactly one case from a multi-case shard", async () => {
  const ctx = await createScenario();
  try {
    const result = await runCase(ctx, "ecr_a");
    assert.equal(result.code, 0, result.stderr);
    const payload = JSON.parse(result.stdout.trim().split("\n").at(-1));
    assert.equal(payload.status, "completed");
    assert.equal(payload.cases, 1);
    assert.equal(payload.objects.length, 2);

    assert.equal(requestsFor(ctx, "ecr_a", "started").length, 1);
    assert.equal(requestsFor(ctx, "ecr_a", "uploads").length, 1);
    assert.equal(requestsFor(ctx, "ecr_a", "completed").length, 1);
    for (const action of ["started", "heartbeat", "uploads", "completed"]) {
      assert.equal(requestsFor(ctx, "ecr_b", action).length, 0, `ecr_b must not ${action}`);
    }
    assert.equal(failureCallbackCount(ctx), 0);
  } finally {
    await ctx.close();
  }
});

test("a replayed case on the same state dir exits before starting the agent", async () => {
  const ctx = await createScenario();
  try {
    const first = await runCase(ctx, "ecr_a");
    assert.equal(first.code, 0, first.stderr);
    const requestsAfterFirst = postRequests(ctx).length;

    const second = await runCase(ctx, "ecr_a");
    assert.equal(second.code, 0, second.stderr);
    assert.equal(statusOf(second), "duplicate_dispatch");
    assert.equal(postRequests(ctx).length, requestsAfterFirst, "replay must not call the platform");
  } finally {
    await ctx.close();
  }
});

test("two different cases run concurrently on the same state dir", async () => {
  const ctx = await createScenario();
  try {
    const [first, second] = await Promise.all([runCase(ctx, "ecr_a"), runCase(ctx, "ecr_b")]);
    assert.equal(first.code, 0, first.stderr);
    assert.equal(second.code, 0, second.stderr);
    assert.deepEqual([statusOf(first), statusOf(second)].sort(), ["completed", "completed"]);
    assert.equal(requestsFor(ctx, "ecr_a", "completed").length, 1);
    assert.equal(requestsFor(ctx, "ecr_b", "completed").length, 1);
    // Per-case claim keys: both claims exist side by side.
    assert.equal(claims(ctx).length, 2);
  } finally {
    await ctx.close();
  }
});

test("a 429 started is capacity-waited with the same event id and then succeeds", async () => {
  // The platform answers 429 while the evaluation is at its hard concurrency
  // cap: the attempt must stay queued, not fail.
  const ctx = await createScenario({ behavior: { startedCapacityFailures: 1 } });
  try {
    const started = Date.now();
    const result = await runCase(ctx, "ecr_a", { RUNNER_CAPACITY_WAIT_SECONDS: "2" });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(statusOf(result), "completed");
    assert.ok(requestsFor(ctx, "ecr_a", "started").length >= 2, "expected a capacity retry");
    assert.equal(failureCallbackCount(ctx), 0, "capacity waiting must never post a failed callback");
    const eventIds = new Set(requestsFor(ctx, "ecr_a", "started").map((request) => request.headers["idempotency-key"]));
    assert.equal(eventIds.size, 1, "retries must reuse the same started event id");
    assert.ok(Date.now() - started >= 1000, "backoff must actually wait");
  } finally {
    await ctx.close();
  }
});

test("capacity waiting is bounded and releases the claim when the budget runs out", async () => {
  const ctx = await createScenario({ behavior: { startedCapacityFailures: 1000 } });
  try {
    const result = await runCase(ctx, "ecr_a", { RUNNER_CAPACITY_WAIT_SECONDS: "1" });
    assert.equal(result.code, 1);
    assert.equal(JSON.parse(result.stderr).error_code, "callback_retryable");
    assert.equal(failureCallbackCount(ctx), 0, "attempt must stay queued, never marked failed");
    assert.equal(requestsFor(ctx, "ecr_a", "completed").length, 0);
    assert.equal(claims(ctx).length, 0, "retryable exit releases the claim");
  } finally {
    await ctx.close();
  }
});

test("a retryable completion failure releases the claim so a re-run proceeds", async () => {
  // Four 5xx completions exhaust the callback client's bounded retries.
  const ctx = await createScenario({ behavior: { completionFailures: 4 } });
  try {
    const first = await runCase(ctx, "ecr_a");
    assert.equal(first.code, 1);
    assert.equal(JSON.parse(first.stderr).error_code, "callback_retryable");
    const failures = ctx.platform.requests.filter((request) => request.path.endsWith("/failed"));
    assert.equal(failures.length, 1);
    assert.equal(failures[0].body.retryable, true);
    assert.equal(claims(ctx).length, 0, "retryable exit releases the claim");

    const second = await runCase(ctx, "ecr_a");
    assert.equal(second.code, 0, second.stderr);
    assert.equal(statusOf(second), "completed");
  } finally {
    await ctx.close();
  }
});

test("a permanent callback failure keeps the claim and blocks the replay", async () => {
  const ctx = await createScenario({ behavior: { failCompletionPermanently: true } });
  try {
    const first = await runCase(ctx, "ecr_a");
    assert.equal(first.code, 1);
    assert.equal(JSON.parse(first.stderr).error_code, "callback_permanent");
    assert.equal(claims(ctx).length, 1, "permanent failure keeps the claim");

    const replay = await runCase(ctx, "ecr_a");
    assert.equal(replay.code, 0);
    assert.equal(statusOf(replay), "duplicate_dispatch");
  } finally {
    await ctx.close();
  }
});

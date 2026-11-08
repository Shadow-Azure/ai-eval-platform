import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createToken } from "../../runner/lib/auth.mjs";
import { validateCompletion, validateFailure, validateStarted } from "../../runner/lib/contracts.mjs";
import { startMockPlatform } from "../../runner/lib/mock-platform.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..");
const script = join(repoRoot, "runner", "scripts", "synthetic-task.mjs");
const workflow = join(repoRoot, "runner", "workflows", "eval-shard.yml");
const secret = "test-secret";
const future = () => new Date(Date.now() + 3_600_000).toISOString();
const sha256 = (text) => `sha256:${createHash("sha256").update(text).digest("hex")}`;

function runTask(args, env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [script, ...args], {
      cwd: repoRoot,
      env: { ...process.env, ...env },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

async function scenario(overrides = {}) {
  let manifestText = "";
  const evalRunId = overrides.evalRunId ?? "eval_test";
  const shardId = overrides.shardId ?? "shard_test";
  const attempt = overrides.attempt ?? 1;
  const token = createToken(
    {
      eval_run_id: evalRunId,
      shard_id: shardId,
      attempt: overrides.tokenAttempt ?? attempt,
      pool_id: "pool_test",
    },
    secret,
    { ttlSeconds: 600 },
  );
  const platform = await startMockPlatform({
    manifestText: () => manifestText,
    expectedToken: token,
    behavior: overrides.behavior ?? {},
  });
  const manifest = {
    schema_version: "1.0",
    eval_run_id: evalRunId,
    shard_id: shardId,
    attempt: overrides.manifestAttempt ?? attempt,
    dispatch_key: `${evalRunId}:${shardId}:${attempt}`,
    callback_base_url: platform.baseUrl,
    deadline: future(),
    case_timeout_seconds: 300,
    max_attempts: 2,
    cases: [
      { case_run_id: "ecr_001", case_key: "case-a", dataset_case_id: "case-a" },
      { case_run_id: "ecr_002", case_key: "case-b", dataset_case_id: "case-b" },
    ],
  };
  manifestText = `${JSON.stringify(manifest, null, 2)}\n`;
  const dispatch = {
    schema_version: "1.0",
    eval_run_id: evalRunId,
    shard_id: shardId,
    attempt,
    dispatch_key: manifest.dispatch_key,
    manifest_url: `${platform.baseUrl}/manifest`,
    manifest_digest: overrides.manifestDigest ?? sha256(manifestText),
    expires_at: future(),
  };
  const dir = mkdtempSync(join(tmpdir(), "m203-"));
  const dispatchPath = join(dir, "dispatch.json");
  writeFileSync(dispatchPath, `${JSON.stringify(dispatch, null, 2)}\n`);
  const stateDir = join(dir, "state");
  const objectRoot = join(dir, "objects");
  return {
    platform,
    manifest,
    dispatch,
    dir,
    dispatchPath,
    stateDir,
    objectRoot,
    token,
    run: (extra = [], envOverrides = {}) =>
      runTask(
        ["--dispatch", dispatchPath, "--state-dir", stateDir, "--object-store-root", objectRoot, ...extra],
        { RUNNER_TASK_TOKEN: token, RUNNER_TASK_SECRET: secret, ...envOverrides },
      ),
    close: () => platform.close(),
  };
}

test("successful synthetic shard uploads deterministic objects and completes every case", async () => {
  const ctx = await scenario();
  try {
    const result = await ctx.run();
    assert.equal(result.code, 0, result.stderr);
    const payload = JSON.parse(result.stdout.trim().split("\n").at(-1));
    assert.equal(payload.status, "completed");
    assert.equal(payload.cases, 2);
    assert.equal(payload.objects.length, 4);

    const completions = ctx.platform.requests.filter((request) => request.path.endsWith("/completed"));
    assert.equal(completions.length, 2);
    for (const request of completions) {
      const validation = validateCompletion(request.body);
      assert.equal(validation.ok, true, validation.errors.join("; "));
      assert.equal(request.headers["idempotency-key"], request.body.event_id);
    }

    for (const object of payload.objects) {
      const file = join(ctx.objectRoot, object.object_key);
      assert.ok(existsSync(file));
      assert.equal(createHash("sha256").update(readFileSync(file)).digest("hex"), object.sha256);
    }
  } finally {
    await ctx.close();
  }
});

test("same dispatch key does not start a second execution", async () => {
  const ctx = await scenario();
  try {
    const first = await ctx.run();
    assert.equal(first.code, 0, first.stderr);
    const completedAfterFirst = ctx.platform.requests.filter((request) => request.path.endsWith("/completed")).length;
    assert.equal(completedAfterFirst, 2);

    const second = await ctx.run();
    assert.equal(second.code, 0, second.stderr);
    assert.match(second.stdout, /duplicate_dispatch/);
    const completedAfterSecond = ctx.platform.requests.filter((request) => request.path.endsWith("/completed")).length;
    assert.equal(completedAfterSecond, 2);
  } finally {
    await ctx.close();
  }
});

test("manifest digest mismatch is rejected before execution", async () => {
  const ctx = await scenario({ manifestDigest: `sha256:${"0".repeat(64)}` });
  try {
    const result = await ctx.run();
    assert.equal(result.code, 2);
    assert.match(result.stderr, /digest/);
    assert.equal(ctx.platform.requests.some((request) => request.method === "POST"), false);
    assert.equal(existsSync(ctx.objectRoot), false);
  } finally {
    await ctx.close();
  }
});

test("token binding mismatch is rejected before any execution side effect", async () => {
  // M2-05: the platform delivers the short-term token WITH the manifest
  // (X-Runner-Task-Token), so verification happens after the fetch but must
  // still precede every claim/callback — no POST, no objects written.
  const ctx = await scenario({ tokenAttempt: 2 });
  try {
    const result = await ctx.run();
    assert.equal(result.code, 2);
    assert.match(result.stderr, /token/);
    assert.equal(ctx.platform.requests.some((request) => request.method === "POST"), false);
    assert.equal(existsSync(ctx.objectRoot), false);
  } finally {
    await ctx.close();
  }
});

test("manifest attempt mismatch is rejected before claim", async () => {
  const ctx = await scenario({ manifestAttempt: 2 });
  try {
    const result = await ctx.run();
    assert.equal(result.code, 2);
    assert.match(result.stderr, /attempt/);
    assert.equal(ctx.platform.requests.some((request) => request.method === "POST"), false);
  } finally {
    await ctx.close();
  }
});

test("retryable completion failure is retried with the same idempotency key", async () => {
  const ctx = await scenario({ behavior: { completionFailures: 2 } });
  try {
    const result = await ctx.run();
    assert.equal(result.code, 0, result.stderr);
    const completions = ctx.platform.requests.filter((request) => request.path.endsWith("/completed"));
    assert.ok(completions.length >= 3, `expected retries, got ${completions.length}`);
    const firstCase = completions.filter((request) => request.body.case_run_id === "ecr_001");
    assert.equal(new Set(firstCase.map((request) => request.headers["idempotency-key"])).size, 1);
  } finally {
    await ctx.close();
  }
});

test("permanent completion failure posts a non-retryable failure callback", async () => {
  const ctx = await scenario({ behavior: { failCompletionPermanently: true } });
  try {
    const result = await ctx.run();
    assert.equal(result.code, 1);
    const failures = ctx.platform.requests.filter((request) => request.path.endsWith("/failed"));
    assert.equal(failures.length, 1);
    const validation = validateFailure(failures[0].body);
    assert.equal(validation.ok, true, validation.errors.join("; "));
    assert.equal(failures[0].body.retryable, false);
    assert.equal(failures[0].body.error_code, "callback_permanent");
  } finally {
    await ctx.close();
  }
});

test("stale attempt returns 409 without infinite retry", async () => {
  const ctx = await scenario({ behavior: { staleAttempt: true, expectedAttempt: 2 } });
  try {
    const result = await ctx.run();
    assert.equal(result.code, 3);
    assert.match(result.stderr, /stale_attempt/);
    const completions = ctx.platform.requests.filter((request) => request.path.endsWith("/completed"));
    assert.equal(completions.length, 1);
    const failures = ctx.platform.requests.filter((request) => request.path.endsWith("/failed"));
    assert.equal(failures.length, 0);
  } finally {
    await ctx.close();
  }
});

test("workflow template reuses the same scripts and has no secret material", () => {
  const text = readFileSync(workflow, "utf8");
  for (const expected of [
    "repository_dispatch",
    "types: [eval-shard]",
    "self-hosted",
    "runner/scripts/synthetic-task.mjs",
    "runner/lib/contracts.mjs",
    "contents: read",
    "id-token: write",
  ]) {
    assert.ok(text.includes(expected), `workflow missing ${expected}`);
  }
  for (const forbidden of ["AKIA", "LTAI", "BEGIN PRIVATE KEY", "sk-"]) {
    assert.equal(text.includes(forbidden), false, `workflow contains ${forbidden}`);
  }
});

test("platform-issued token (manifest header) drives the loop without env secrets", async () => {
  // M2-05 real loop: no RUNNER_TASK_TOKEN / RUNNER_TASK_SECRET in the job env.
  // The platform hands the short-term token to the runner via the manifest
  // response; binding checks still run against the dispatch.
  const ctx = await scenario();
  delete ctx.token;
  try {
    const result = await ctx.run(
      [],
      { RUNNER_TASK_TOKEN: "", RUNNER_TASK_SECRET: "" },
    );
    assert.equal(result.code, 0, result.stderr);
    const payload = JSON.parse(result.stdout.trim().split("\n").at(-1));
    assert.equal(payload.status, "completed");

    const started = ctx.platform.requests.filter((request) => request.path.endsWith("/started"));
    assert.equal(started.length, 2);
    for (const request of started) {
      const validation = validateStarted(request.body);
      assert.equal(validation.ok, true, validation.errors.join("; "));
    }
    const heartbeats = ctx.platform.requests.filter((request) => request.path.endsWith("/heartbeat"));
    assert.equal(heartbeats.length, 2);
    // started precedes heartbeat for each case
    const paths = ctx.platform.requests.map((request) => request.path);
    for (const caseRunId of ["ecr_001", "ecr_002"]) {
      assert.ok(paths.indexOf(`/runner/v1/case-runs/${caseRunId}/started`) < paths.indexOf(`/runner/v1/case-runs/${caseRunId}/heartbeat`));
    }
  } finally {
    await ctx.close();
  }
});

test("presign upload mode PUTs bytes to platform keys and reports them", async () => {
  const ctx = await scenario({ behavior: { uploadUrls: true } });
  try {
    const result = await ctx.run(
      [],
      { RUNNER_PRESIGN_UPLOADS: "1" },
    );
    assert.equal(result.code, 0, result.stderr);
    const payload = JSON.parse(result.stdout.trim().split("\n").at(-1));
    assert.equal(payload.status, "completed");
    assert.equal(payload.objects.length, 4);
    for (const object of payload.objects) {
      assert.ok(object.object_key.startsWith("mock/"), `platform key expected: ${object.object_key}`);
      const bytes = ctx.platform.objects.get(object.object_key.slice("mock/".length) ? object.object_key : object.object_key);
      assert.ok(bytes, `PUT body recorded for ${object.object_key}`);
      assert.equal(createHash("sha256").update(bytes).digest("hex"), object.sha256);
    }

    const uploads = ctx.platform.requests.filter((request) => request.path.endsWith("/uploads"));
    assert.equal(uploads.length, 2);
    for (const request of uploads) {
      for (const object of request.body.objects) {
        assert.ok(object.object_key.startsWith("mock/"));
      }
    }
    const completions = ctx.platform.requests.filter((request) => request.path.endsWith("/completed"));
    for (const request of completions) {
      assert.ok(request.body.score_manifest.object_key.startsWith("mock/"));
    }
  } finally {
    await ctx.close();
  }
});

test("synthetic result content is deterministic across independent runs", async () => {
  const first = await scenario({ shardId: "shard_one" });
  const second = await scenario({ shardId: "shard_one" });
  try {
    const firstRun = await first.run();
    const secondRun = await second.run();
    assert.equal(firstRun.code, 0, firstRun.stderr);
    assert.equal(secondRun.code, 0, secondRun.stderr);
    const firstPayload = JSON.parse(firstRun.stdout.trim().split("\n").at(-1));
    const secondPayload = JSON.parse(secondRun.stdout.trim().split("\n").at(-1));
    const firstScore = join(first.objectRoot, firstPayload.objects[0].object_key);
    const secondScore = join(second.objectRoot, secondPayload.objects[0].object_key);
    assert.equal(readFileSync(firstScore, "utf8"), readFileSync(secondScore, "utf8"));
    const firstTrace = join(first.objectRoot, firstPayload.objects[1].object_key);
    const secondTrace = join(second.objectRoot, secondPayload.objects[1].object_key);
    assert.equal(readFileSync(firstTrace, "utf8"), readFileSync(secondTrace, "utf8"));
  } finally {
    await first.close();
    await second.close();
  }
});

import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { validateHeartbeat } from "../../runner/lib/contracts.mjs";
import { createScenario, repoRoot, runNode, taskSecret } from "./scenario-helpers.mjs";

/**
 * M4-02 (#79) integration: prove `real-task.mjs` keeps a heartbeat sidecar
 * alive while the synchronous agent path runs, and stops it on both the
 * success and the exception path. Fake `docker`/`git` binaries keep the real
 * SWE-bench flow (create/cp/exec/diff/apply/test) offline; no model is called.
 */

const script = join(repoRoot, "runner", "scripts", "real-task.mjs");
const CASE_RUN_ID = "ecr_real";

const cases = [{ case_run_id: CASE_RUN_ID, case_key: "case-real", dataset_case_id: "case-real" }];

function tasksPayload() {
  return {
    model: { name: "claude-sonnet-4", anthropic_base_url: "http://127.0.0.1:9" },
    adapter_key: "swebench",
    runtime: { key: "claude-code" },
    // real-task reads the image digests from the payload top level.
    environment_image: "eval/swebench:test",
    cases: [
      {
        case_run_id: CASE_RUN_ID,
        case_key: "case-real",
        repo: "example/repo",
        base_commit: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        prompt: "fix the failing test",
        environment_image: "eval/swebench:test",
        test_patch: "",
        expected_metrics: { fail_to_pass: ["tests/test_bug.py::test_fix"], pass_to_pass: [] },
      },
    ],
  };
}

/**
 * fake `docker` for the happy path: the agent exec sleeps long enough for the
 * sidecar to beat, the container diff produces a patch and pytest reports the
 * expected F2P test. `fake git` makes the host-side checkout succeed.
 */
function installFakeBin({ failing = false, agentSleepSeconds = 0.6 } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "fake-docker-"));
  const docker = failing
    ? `#!/bin/sh
sleep ${agentSleepSeconds}
exit 1
`
    : `#!/bin/sh
case "$*" in
  *claude-code-smoke*) sleep ${agentSleepSeconds}; exit 0 ;;
  *git*diff*) printf '%s\n' "diff --git a/app.py b/app.py" "--- a/app.py" "+++ b/app.py" "+fixed"; exit 0 ;;
  *pytest*) printf '%s\n' "PASSED tests/test_bug.py::test_fix"; exit 0 ;;
esac
exit 0
`;
  const git = "#!/bin/sh\nexit 0\n";
  for (const [name, body] of [["docker", docker], ["git", git]]) {
    const file = join(dir, name);
    writeFileSync(file, body, { mode: 0o755 });
    chmodSync(file, 0o755);
  }
  return dir;
}

const caseArgs = (ctx) => [
  "--dispatch", ctx.dispatchPath,
  "--case", CASE_RUN_ID,
  "--state-dir", ctx.stateDir,
  "--object-store-root", ctx.objectRoot,
];

const runCase = (ctx, fakeBin, env = {}) =>
  runNode(script, caseArgs(ctx), {
    RUNNER_TASK_TOKEN: ctx.token,
    RUNNER_TASK_SECRET: taskSecret,
    PATH: `${fakeBin}:${process.env.PATH}`,
    ...env,
  });

const requestsFor = (ctx, action) =>
  ctx.platform.requests.filter((request) => request.path === `/runner/v1/case-runs/${CASE_RUN_ID}/${action}`);

const indexOfLast = (ctx, predicate) =>
  ctx.platform.requests.reduce((last, request, index) => (predicate(request) ? index : last), -1);

const statusOf = (result) => JSON.parse(result.stdout.trim().split("\n").at(-1)).status;

test("a real case keeps beating while the agent runs and stops the sidecar before completion", async () => {
  const fakeBin = installFakeBin();
  const ctx = await createScenario({ cases, behavior: { tasks: tasksPayload(), modelToken: "model-token-test" } });
  try {
    // RUNNER_ID is set in the job env: it must not leak into the heartbeat
    // payload (the heartbeat schema has additionalProperties:false).
    const result = await runCase(ctx, fakeBin, { RUNNER_ID: "fixed-linux-eval" });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(statusOf(result), "completed");

    const heartbeats = requestsFor(ctx, "heartbeat");
    const sidecarBeats = heartbeats.filter((request) => request.body.sequence >= 2);
    assert.ok(sidecarBeats.length >= 1, "the sidecar must beat while realCase blocks the control script");
    for (const beat of sidecarBeats) {
      // The sidecar sends the real case attempt, not a synthetic one.
      assert.equal(beat.body.case_run_id, CASE_RUN_ID);
      assert.equal(beat.body.attempt, 1);
      assert.equal(Object.hasOwn(beat.body, "runner_id"), false);
      assert.equal(beat.headers.authorization, `Bearer ${ctx.token}`);
      assert.deepEqual(validateHeartbeat(beat.body), { ok: true, errors: [], kind: "heartbeat" });
    }

    // The single synchronous heartbeat (sequence 1) is still sent first.
    assert.equal(heartbeats[0].body.sequence, 1);

    // The sidecar is stopped in realCase's finally, i.e. before the uploads and
    // completion callbacks, so it can never race a terminal callback.
    const lastBeatIndex = indexOfLast(ctx, (request) => request.path.endsWith("/heartbeat"));
    const completedIndex = indexOfLast(ctx, (request) => request.path.endsWith("/completed"));
    const uploadsIndex = indexOfLast(ctx, (request) => request.path.endsWith("/uploads"));
    assert.ok(uploadsIndex >= 0 && completedIndex >= 0);
    assert.ok(lastBeatIndex < uploadsIndex, "no heartbeat after the sidecar was stopped");
    assert.ok(lastBeatIndex < completedIndex);
    assert.ok(
      requestsFor(ctx, "completed")[0].body.sequence >
        Math.max(...heartbeats.map((request) => request.body.sequence)),
      "completion sequence must remain after every heartbeat sequence",
    );
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

test("an exception while the sidecar runs still stops it and keeps the case failure", async () => {
  const fakeBin = installFakeBin({ failing: true, agentSleepSeconds: 2.3 });
  const ctx = await createScenario({ cases, behavior: { tasks: tasksPayload(), modelToken: "model-token-test" } });
  try {
    const result = await runCase(ctx, fakeBin, { RUNNER_HEARTBEAT_INTERVAL_MS: "1000" });
    assert.equal(result.code, 1, "a docker failure must still fail the case");
    assert.equal(JSON.parse(result.stderr).error_code, "runtime_error");
    assert.equal(requestsFor(ctx, "completed").length, 0);

    const beatsAtExit = requestsFor(ctx, "heartbeat").length;
    assert.ok(beatsAtExit >= 2, "the sidecar must have been spawned for the real case");
    // A sidecar left behind would keep beating every 1s (the configured floor);
    // wait past one interval and make sure the count is frozen.
    await new Promise((resolve) => setTimeout(resolve, 1600));
    assert.equal(requestsFor(ctx, "heartbeat").length, beatsAtExit, "the sidecar must not survive the failed case");
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

test("a completion callback failure reports failure after every heartbeat sequence", async () => {
  const fakeBin = installFakeBin({ agentSleepSeconds: 2.3 });
  const ctx = await createScenario({
    cases,
    behavior: {
      tasks: tasksPayload(),
      modelToken: "model-token-test",
      completionFailures: 4,
    },
  });
  try {
    const result = await runCase(ctx, fakeBin, { RUNNER_HEARTBEAT_INTERVAL_MS: "1000" });
    assert.equal(result.code, 1, result.stderr);
    const heartbeats = requestsFor(ctx, "heartbeat");
    const failures = requestsFor(ctx, "failed");
    assert.ok(heartbeats.length >= 3);
    assert.equal(failures.length, 1);
    assert.ok(
      failures[0].body.sequence > Math.max(...heartbeats.map((request) => request.body.sequence)),
      "terminal failure sequence must remain after every heartbeat sequence",
    );
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

test("RUNNER_HEARTBEAT_DISABLE=1 keeps the old single-heartbeat behaviour", async () => {
  const fakeBin = installFakeBin();
  const ctx = await createScenario({ cases, behavior: { tasks: tasksPayload(), modelToken: "model-token-test" } });
  try {
    const result = await runCase(ctx, fakeBin, { RUNNER_HEARTBEAT_DISABLE: "1" });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(statusOf(result), "completed");
    const heartbeats = requestsFor(ctx, "heartbeat");
    assert.equal(heartbeats.length, 1, "disabled: only the synchronous sequence 1 heartbeat");
    assert.equal(heartbeats[0].body.sequence, 1);
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createScenario, repoRoot, runNode, taskSecret } from "./scenario-helpers.mjs";

/**
 * M6-01 (#90): a failure while applying the official test patch must not lose
 * the native trace captured during the agent run. The fake `docker` writes the
 * native files on container start and rejects `git apply`, so the scoring
 * block throws after the agent already produced its session.
 */

const script = join(repoRoot, "runner", "scripts", "real-task.mjs");
const CASE_RUN_ID = "ecr_real";

const cases = [{ case_run_id: CASE_RUN_ID, case_key: "case-real", dataset_case_id: "case-real" }];

function tasksPayload() {
  return {
    model: { name: "claude-sonnet-4", anthropic_base_url: "http://127.0.0.1:9" },
    adapter_key: "swebench",
    runtime: { key: "claude-code" },
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

function installFakeBin() {
  const dir = mkdtempSync(join(tmpdir(), "fake-docker-trace-"));
  const docker = `#!/bin/sh
host=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "-v" ]; then
    case "$arg" in
      *:/testbed) host="\${arg%:/testbed}" ;;
    esac
  fi
  prev="$arg"
done
case "$*" in
  "create "*) echo fake-container; exit 0 ;;
  "cp "*) exit 0 ;;
  *"exec"*"chmod -R a+rwX /testbed"*) chmod -R a+rwX "$(cat "$(dirname "$0")/host")"; exit 0 ;;
  *"git apply"*) echo "patch does not apply" >&2; exit 1 ;;
  *"claude --version"*) echo "2.1.278 (Claude Code)"; exit 0 ;;
  *"git"*" diff"*) printf '%s\\n' "diff --git a/app.py b/app.py" "--- a/app.py" "+++ b/app.py" "+fixed"; exit 0 ;;
  *"git"*" add"*) exit 0 ;;
  *"-d --name"*)
    printf '%s' "$host" > "$(dirname "$0")/host"
    mkdir -p "$host/trace" "$host/.runtime-home/.claude/projects/-testbed"
    printf '%s\\n' '{"type":"user","sessionId":"s1"}' > "$host/.runtime-home/.claude/projects/-testbed/s1.jsonl"
    printf '%s\\n' '{"type":"result","subtype":"success"}' > "$host/trace/claude-stream.jsonl"
    chmod 000 "$host/.runtime-home/.claude/projects/-testbed/s1.jsonl"
    chmod 000 "$host/.runtime-home/.claude/projects/-testbed"
    chmod 000 "$host/.runtime-home/.claude"
    chmod 000 "$host/.runtime-home"
    exit 0
    ;;
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

const requestsFor = (ctx, action) =>
  ctx.platform.requests.filter((request) => request.path === `/runner/v1/case-runs/${CASE_RUN_ID}/${action}`);

test("a test-patch apply failure still uploads the captured native trace", async () => {
  const fakeBin = installFakeBin();
  const ctx = await createScenario({ cases, behavior: { tasks: tasksPayload(), modelToken: "model-token-test" } });
  try {
    const result = await runNode(script, caseArgs(ctx), {
      RUNNER_TASK_TOKEN: ctx.token,
      RUNNER_TASK_SECRET: taskSecret,
      PATH: `${fakeBin}:${process.env.PATH}`,
      RUNNER_HEARTBEAT_DISABLE: "1",
    });
    assert.equal(result.code, 0, result.stderr);

    const completed = requestsFor(ctx, "completed");
    assert.equal(completed.length, 1, "the case must finish through the completed callback");
    assert.equal(requestsFor(ctx, "failed").length, 0);
    assert.equal(completed[0].body.execution_status, "failed");
    assert.equal(completed[0].body.result_status, "not_scored");

    const paths = new Set(
      (completed[0].body.trace_manifest?.objects ?? []).map((object) => object.logical_path),
    );
    assert.ok(paths.has("native/projects/-testbed/s1.jsonl"), [...paths].join(", "));
    assert.ok(paths.has("diagnostics/stdout.jsonl"), [...paths].join(", "));
    assert.ok(paths.has("diagnostics/scoring-failure.json"), [...paths].join(", "));
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

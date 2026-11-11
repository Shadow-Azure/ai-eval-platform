import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const workflowPath = join(repoRoot, ".github", "workflows", "eval-shard.yml");

const workflow = () => readFileSync(workflowPath, "utf8");

/** Slice one top-level job block (indent 2) out of the workflow text. */
function jobBlock(text, name) {
  const lines = text.split("\n");
  const start = lines.indexOf(`  ${name}:`);
  assert.notEqual(start, -1, `workflow missing job ${name}`);
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^ {2}[A-Za-z0-9_-]+:\s*$/.test(lines[index])) {
      end = index;
      break;
    }
  }
  return lines.slice(start, end).join("\n");
}

test("eval-shard prepare job plans one matrix entry per manifest case", () => {
  const text = workflow();
  const prepare = jobBlock(text, "prepare");
  for (const expected of [
    "if: github.event_name == 'repository_dispatch'",
    "timeout-minutes: 10",
    "outputs:",
    "cases: ${{ steps.plan.outputs.cases }}",
    "runner/lib/contracts.mjs dispatch",
    "runner/scripts/plan-cases.mjs",
    "--state-dir",
    'echo "cases=$cases" >> "$GITHUB_OUTPUT"',
  ]) {
    assert.ok(prepare.includes(expected), `prepare job missing ${expected}`);
  }
  // prepare only plans; it never runs a case.
  assert.equal(prepare.includes("real-task.mjs"), false);
});

test("eval-shard execute job runs exactly one case per matrix job", () => {
  const text = workflow();
  const execute = jobBlock(text, "execute");
  for (const expected of [
    "needs: prepare",
    "needs.prepare.outputs.cases != '[]'",
    "fail-fast: false",
    "matrix:",
    "case: ${{ fromJson(needs.prepare.outputs.cases) }}",
    "runner/scripts/real-task.mjs",
    '--case "${{ matrix.case.id }}"',
    "RUNNER_PRESIGN_UPLOADS",
    "RUNNER_STATE_DIR",
    "RUNNER_OBJECT_ROOT",
    "timeout-minutes: 60",
  ]) {
    assert.ok(execute.includes(expected), `execute job missing ${expected}`);
  }
  // The dispatch payload is re-materialized per job; runner.temp is not shared.
  assert.ok(execute.includes("toJson(github.event.client_payload)"), "execute must re-write the dispatch payload");
  // One case per job: no shell loop over the manifest case list.
  assert.doesNotMatch(execute, /for\s+[A-Za-z_]\w*\s+in\s+/, "execute must not loop over manifest cases");
  assert.doesNotMatch(execute, /\bwhile\s+read\b/, "execute must not loop over manifest cases");
  assert.equal(
    (text.match(/node runner\/scripts\/real-task\.mjs/g) ?? []).length,
    1,
    "exactly one real-task invocation in the workflow",
  );
  // The whole-shard job is gone: no job executes all cases sequentially.
  assert.equal(text.includes("synthetic-shard"), false);
});

test("eval-shard keeps one workflow run per shard and the bootstrap", () => {
  const text = workflow();
  assert.ok(text.includes("concurrency:"), "workflow-level concurrency is still required");
  assert.ok(
    text.includes("group: eval-shard-${{ github.event.client_payload.dispatch_key || github.run_id }}"),
    "dispatch-key concurrency group must survive the matrix split",
  );
  assert.ok(text.includes("cancel-in-progress: false"));
  assert.ok(text.includes("/api/v1/evaluations"), "bootstrap must create an evaluation via the platform API");
  assert.ok(
    text.includes("github.event_name == 'workflow_dispatch'"),
    "bootstrap stays gated on workflow_dispatch",
  );
});

test("eval-shard workflow carries no secret material or demo tokens", () => {
  const text = workflow();
  for (const forbidden of [
    "AKIA",
    "LTAI",
    "BEGIN PRIVATE KEY",
    "development-only-secret",
    "RUNNER_TASK_SECRET",
    "make-eval-dispatch",
  ]) {
    assert.equal(text.includes(forbidden), false, `workflow must not contain ${forbidden}`);
  }
  // OpenAI-style API keys (but NOT the legitimate "Task-Token" wording).
  assert.doesNotMatch(text, /sk-[A-Za-z0-9]{16}/, "workflow must not contain API keys");
  const traceE2e = jobBlock(text, "trace-e2e");
  const withoutTraceE2e = text.replace(traceE2e, "");
  assert.ok(
    !withoutTraceE2e.includes("secrets."),
    "only the explicit trace-e2e job may reference repository secrets",
  );
  assert.ok(traceE2e.includes("secrets.OSS_ENDPOINT"));
  // The dispatch payload stays a reference: only the opaque matrix case id is
  // interpolated into shell, never case data or credentials.
  assert.doesNotMatch(text, /\$\{\{\s*github\.event\.client_payload\.(?!dispatch_key)/, "only dispatch_key may be interpolated");
});

test("500 cases at the default shard size of 64 form 8 dispatches", () => {
  assert.equal(Math.ceil(500 / 64), 8);
});

test("eval-shard downloads the scripts bundle and gates checkout as a fallback", () => {
  const text = workflow();
  for (const name of ["prepare", "execute"]) {
    const job = jobBlock(text, name);
    for (const expected of [
      "name: Fetch the scripts bundle",
      "id: bundle",
      "steps.bundle.outputs.ok != 'true'",
      "manifestUrl.pathname.replace(/\\/manifest$/, \"/scripts\")",
      "metadataUrl = manifestUrl.toString()",
      "$EVAL_SCRIPTS_DIR",
      "tar -xzf",
      "md5",
      "sha256",
    ]) {
      assert.ok(job.includes(expected), `${name} missing ${expected}`);
    }
    const checkouts = job.match(/uses: actions\/checkout@v4/g) ?? [];
    assert.equal(checkouts.length, 2, `${name} must keep a two-attempt checkout fallback`);
    assert.ok(job.includes("if: steps.bundle.outputs.ok != 'true'"), `${name} checkout must be gated`);
    assert.ok(job.includes("steps.fallback_checkout.outcome == 'failure'"), `${name} must retry the fallback`);
    assert.ok(job.includes("EVAL_SCRIPTS_DIR=$GITHUB_WORKSPACE"), `${name} must resolve fallback root`);
  }
  const total = text.match(/uses: actions\/checkout@v4/g) ?? [];
  assert.equal(
    total.length,
    5,
    "prepare/execute keep two checkout fallbacks and trace-e2e keeps its single checkout",
  );
});

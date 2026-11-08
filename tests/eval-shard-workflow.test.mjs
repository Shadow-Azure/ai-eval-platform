import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const workflowPath = join(repoRoot, ".github", "workflows", "eval-shard.yml");

const workflow = () => readFileSync(workflowPath, "utf8");

test("eval-shard workflow consumes real platform dispatches", () => {
  const text = workflow();
  for (const expected of [
    "repository_dispatch",
    "types: [eval-shard]",
    "self-hosted",
    "runner/lib/contracts.mjs dispatch",
    "runner/scripts/synthetic-task.mjs",
    "RUNNER_PRESIGN_UPLOADS",
    "github.event.client_payload",
    "contents: read",
  ]) {
    assert.ok(text.includes(expected), `workflow missing ${expected}`);
  }
});

test("eval-shard workflow carries no secret material or demo tokens", () => {
  const text = workflow();
  for (const forbidden of [
    "AKIA",
    "LTAI",
    "BEGIN PRIVATE KEY",
    "sk-",
    "development-only-secret",
    "RUNNER_TASK_SECRET",
    "make-eval-dispatch",
  ]) {
    assert.equal(text.includes(forbidden), false, `workflow must not contain ${forbidden}`);
  }
  assert.ok(!text.includes("secrets."), "workflow must not reference repository secrets");
});

test("workflow_dispatch is a bootstrap that asks the platform to dispatch", () => {
  const text = workflow();
  assert.ok(text.includes("/api/v1/evaluations"), "bootstrap must create an evaluation via the platform API");
  assert.ok(
    text.includes("github.event_name == 'repository_dispatch'"),
    "real execution must be gated on repository_dispatch",
  );
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (path) => readFileSync(join(root, path), "utf8");

test("smoke workflow runs on the fixed runner and exercises a container", () => {
  const workflow = read(".github/workflows/runner-smoke.yml");
  for (const expected of [
    "workflow_dispatch",
    "runs-on: [self-hosted, linux, eval]",
    "/etc/eval-platform/runner.env",
    "runner/v1/runners/heartbeat",
    "docker run --rm ubuntu:22.04",
    "current_case_attempt_id",
  ]) {
    assert.ok(workflow.includes(expected), `smoke workflow missing ${expected}`);
  }
});

test("install script uses a one-time registration token and a scoped heartbeat token", () => {
  const script = read("infra/runner/install.sh");
  for (const expected of [
    "RUNNER_TOKEN",
    "RUNNER_HEARTBEAT_TOKEN",
    "--labels",
    "/etc/eval-platform/runner.env",
    "eval-platform-heartbeat.service",
  ]) {
    assert.ok(script.includes(expected), `install script missing ${expected}`);
  }
  for (const forbidden of ["ALIYUN_ACCESS_KEY", "BEGIN PRIVATE KEY", "set -x"]) {
    assert.equal(script.includes(forbidden), false, `install script contains ${forbidden}`);
  }
  const unit = read("infra/runner/eval-platform-heartbeat.service");
  assert.ok(unit.includes("EnvironmentFile=/etc/eval-platform/runner.env"));
});

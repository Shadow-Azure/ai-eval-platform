import assert from "node:assert/strict";
import test from "node:test";

import { buildCaseTask, buildCaseTasks } from "../../runner/lib/task-payload.mjs";

const payload = {
  model: { name: "MiniMax-M2.7", anthropic_base_url: "http://gateway/anthropic" },
  execution_image_digest: "sha256:" + "a".repeat(64),
  scoring_image_digest: "sha256:" + "b".repeat(64),
  environment_image: "swebench/env:latest",
  adapter_key: "swebench",
  runtime: { key: "claude-code", version: "2.x" },
  asset_snapshot: {
    snapshot_id: "snap-1",
    resolved_commit: "c".repeat(40),
    asset_count: 1,
    bundle_url: "http://objects/bundle.json.gz",
    bundle_digest: "sha256:" + "d".repeat(64),
  },
  cases: [
    { case_run_id: "case-1", case_key: "a", prompt: "fix" },
    { case_run_id: "case-2", case_key: "b", prompt: "fix" },
  ],
};

test("run-wide asset snapshot is copied onto every case task", () => {
  const tasks = buildCaseTasks(payload);
  assert.equal(tasks.size, 2);
  for (const task of tasks.values()) {
    assert.equal(task.asset_snapshot.snapshot_id, "snap-1");
    assert.equal(task.asset_snapshot.bundle_url, "http://objects/bundle.json.gz");
    assert.equal(task.runtime_key, "claude-code");
    assert.equal(task.model.name, "MiniMax-M2.7");
  }
  assert.equal(tasks.get("case-2").case_key, "b");
});

test("missing asset snapshot is an explicit empty object, never undefined", () => {
  const task = buildCaseTask({ case_run_id: "case-1" }, { cases: [] });
  assert.deepEqual(task.asset_snapshot, {});
  assert.equal(task.egress_proxy, null);
  assert.equal(task.adapter_key, "swebench");
  assert.equal(task.runtime_key, "claude-code");
});

test("run-wide egress proxy is copied onto every case task", () => {
  const tasks = buildCaseTasks({ ...payload, egress_proxy: "http://10.0.0.10:3128" });
  for (const task of tasks.values()) {
    assert.equal(task.egress_proxy, "http://10.0.0.10:3128");
  }
});

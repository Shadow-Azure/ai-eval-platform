import test from "node:test";
import assert from "node:assert/strict";
import { getDatasetAdapter, getRuntimeAdapter, datasetAdapterKeys, runtimeAdapterKeys } from "../../runner/lib/adapters.mjs";

test("registry exposes the built-in plugins", () => {
  assert.deepEqual(datasetAdapterKeys(), ["swebench"]);
  assert.deepEqual(runtimeAdapterKeys(), ["claude-code"]);
  assert.throws(() => getDatasetAdapter("missing"), /unsupported dataset adapter/);
  assert.throws(() => getRuntimeAdapter("missing"), /unsupported runtime adapter/);
});

test("SWE-bench adapter resolves environment image and official report", () => {
  const adapter = getDatasetAdapter("swebench");
  const task = {
    case_key: "astropy__astropy-12907",
    environment_image: "registry/sweb:latest",
    expected_metrics: { fail_to_pass: ["t::a"], pass_to_pass: ["t::b"] },
  };
  assert.equal(adapter.environmentImage(task), "registry/sweb:latest");
  const report = adapter.parseReport(task, ["PASSED t::a", "PASSED t::b"].join("\n"));
  assert.equal(report.resultStatus, "resolved");
  assert.deepEqual(report.score[task.case_key].tests_status.FAIL_TO_PASS.success, ["t::a"]);
});

test("SWE-bench adapter reports unresolved when F2P fails", () => {
  const adapter = getDatasetAdapter("swebench");
  const task = {
    case_key: "astropy__astropy-12907",
    expected_metrics: { fail_to_pass: ["t::a"], pass_to_pass: ["t::b"] },
  };
  const report = adapter.parseReport(task, ["FAILED t::a", "PASSED t::b"].join("\n"));
  assert.equal(report.resultStatus, "unresolved");
  assert.deepEqual(report.score[task.case_key].tests_status.FAIL_TO_PASS.failure, ["t::a"]);
});

test("Claude Code runtime installs from npm mirror and launches smoke", () => {
  const adapter = getRuntimeAdapter("claude-code");
  assert.match(adapter.installScript(), /registry\.npmmirror\.com/);
  assert.match(adapter.agentScript(), /claude-code-smoke\.mjs/);
});

test("Claude Code task template is dataset-agnostic and injects constraints", () => {
  const runtime = getRuntimeAdapter("claude-code");
  const prompt = runtime.buildPrompt({
    problem: "fix the separator",
    workspace: "/testbed",
    constraints: ["do not edit tests"],
  });
  assert.match(prompt, /autonomous software engineer/);
  assert.match(prompt, /\/testbed/);
  assert.match(prompt, /fix the separator/);
  assert.match(prompt, /do not edit tests/);
  assert.doesNotMatch(prompt, /astropy/);
});

test("SWE-bench adapter supplies workspace and constraints", () => {
  const dataset = getDatasetAdapter("swebench");
  assert.equal(dataset.workspacePath(), "/testbed");
  assert.ok(dataset.constraints().some((item) => item.includes("test files")));
});

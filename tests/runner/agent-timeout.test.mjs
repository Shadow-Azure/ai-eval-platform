import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { validatePayload } from "../../runner/lib/contracts.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "..", "runner", "scripts", "real-task.mjs"), "utf8");
const manifestFixture = JSON.parse(
  readFileSync(join(here, "..", "fixtures", "runner", "manifest.valid.json"), "utf8"),
);

test("agent wall-clock timeout defaults to 30 minutes and stays overridable", () => {
  assert.ok(source.includes("process.env.REAL_TASK_TIMEOUT_MS || String(agentTimeoutMs)"));
  assert.ok(source.includes("agentTimeoutSeconds || task.agent_timeout_seconds || 1800"));
  assert.ok(source.includes("Math.max(1_900_000, agentTimeoutMs + 120_000)"));
  // The manifest value wins; older platforms still fall back to the task row.
  assert.ok(source.includes("agentTimeoutSeconds: manifest.agent_timeout_seconds"));
});

test("manifest Agent timeout is optional and bounded to 60..86400 seconds", () => {
  for (const value of [60, 1800, 86400]) {
    const result = validatePayload("manifest", {
      ...manifestFixture,
      agent_timeout_seconds: value,
    });
    assert.equal(result.ok, true, `${value}: ${result.errors.join("; ")}`);
  }
  for (const value of [0, 59, 86401]) {
    const result = validatePayload("manifest", {
      ...manifestFixture,
      agent_timeout_seconds: value,
    });
    assert.equal(result.ok, false);
    assert.match(result.errors.join("; "), /agent_timeout_seconds/);
  }
  assert.equal(validatePayload("manifest", manifestFixture).ok, true);
});

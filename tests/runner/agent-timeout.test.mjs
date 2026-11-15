import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "..", "runner", "scripts", "real-task.mjs"), "utf8");

test("agent wall-clock timeout defaults to 30 minutes and stays overridable", () => {
  assert.ok(source.includes('process.env.REAL_TASK_TIMEOUT_MS || "1800000"'));
  assert.ok(source.includes("process.env.REAL_TASK_AGENT_TIMEOUT_MS || 1_900_000"));
});

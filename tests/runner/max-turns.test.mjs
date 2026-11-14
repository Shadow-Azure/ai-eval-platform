import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "..", "runner", "scripts", "real-task.mjs"), "utf8");

test("complex command-flow evals default to 80 turns and stay overridable", () => {
  assert.ok(source.includes('process.env.REAL_TASK_MAX_TURNS || "80"'));
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  join(here, "..", "..", "runner", "adapters", "runtimes", "claude-code.mjs"),
  "utf8",
);

test("agent PATH exposes the source-mode codespec CLI", () => {
  assert.ok(source.includes("/asset-source/bin"));
  assert.ok(source.includes("/asset-source/node_modules/.bin"));
  assert.ok(source.includes("/home/eval/bin"));
  assert.ok(source.includes("ln -sf /asset-source/bin/codespec.js /asset-source/bin/codespec"));
});

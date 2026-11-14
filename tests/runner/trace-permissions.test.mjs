import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "..", "runner", "scripts", "real-task.mjs"), "utf8");

test("agent-writable trace/runtime directories are created before the recursive chmod", () => {
  const runtime = source.indexOf('mkdirSync(join(workdir, ".runtime-home")');
  const trace = source.indexOf('mkdirSync(join(workdir, "trace")');
  const chmod = source.indexOf('spawnSync("chmod", ["-R", "a+rwX", workdir]');
  assert.ok(runtime >= 0, "runtime-home creation missing");
  assert.ok(trace >= 0, "trace creation missing");
  assert.ok(chmod >= 0, "recursive workdir chmod missing");
  assert.ok(runtime < chmod, "runtime-home must be created before chmod");
  assert.ok(trace < chmod, "trace must be created before chmod");
});

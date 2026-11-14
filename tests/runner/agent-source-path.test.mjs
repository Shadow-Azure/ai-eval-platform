import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const helper = join(root, "runner", "scripts", "link-source-bins.mjs");
const adapterSource = readFileSync(
  join(root, "runner", "adapters", "runtimes", "claude-code.mjs"),
  "utf8",
);

test("agent PATH exposes source CLI directories and the generic linker", () => {
  assert.ok(adapterSource.includes("/home/eval/bin"));
  assert.ok(adapterSource.includes("/asset-source/bin"));
  assert.ok(adapterSource.includes("/asset-source/node_modules/.bin"));
  assert.ok(adapterSource.includes("link-source-bins.mjs"));
});

test("source package bin entries become executable wrappers even when source is 0644", () => {
  const work = mkdtempSync(join(tmpdir(), "source-bins-"));
  const source = join(work, "source");
  const binDir = join(work, "bin");
  mkdirSync(join(source, "bin"), { recursive: true });
  writeFileSync(join(source, "bin", "demo.js"), "console.log('demo');\n", { mode: 0o644 });
  writeFileSync(join(source, "bin", "other.js"), "console.log('other');\n", { mode: 0o644 });
  writeFileSync(
    join(source, "package.json"),
    JSON.stringify({ name: "@acme/demo", bin: { demo: "./bin/demo.js", other: "./bin/other.js" } }),
  );
  const result = spawnSync(process.execPath, [helper, source, binDir], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  for (const name of ["demo", "other"]) {
    const wrapper = join(binDir, name);
    assert.ok(existsSync(wrapper), `missing ${name}`);
    assert.ok(readFileSync(wrapper, "utf8").includes(join(source, "bin", `${name}.js`)));
    const run = spawnSync(wrapper, [], { encoding: "utf8" });
    assert.equal(run.status, 0, run.stderr);
    assert.equal(run.stdout.trim(), name);
  }
});

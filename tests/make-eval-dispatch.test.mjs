import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { verifyToken } from "../runner/lib/auth.mjs";
import { validateDispatch, validateManifest } from "../runner/lib/contracts.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "..", "scripts", "make-eval-dispatch.mjs");
const TASK_SECRET = "development-only-secret";

function makeDispatch(outDir, extraArgs = []) {
  execFileSync(process.execPath, [script, "--out-dir", outDir, ...extraArgs], {
    encoding: "utf8",
    env: { ...process.env, RUNNER_TASK_SECRET: TASK_SECRET },
  });
}

test("generated dispatch and manifest satisfy the v1 contract", (t) => {
  const outDir = mkdtempSync(join(tmpdir(), "eval-dispatch-"));
  t.after(() => rmSync(outDir, { recursive: true, force: true }));

  makeDispatch(outDir, ["--callback-base-url", "http://127.0.0.1:8000"]);

  const dispatch = JSON.parse(readFileSync(join(outDir, "dispatch.json"), "utf8"));
  const manifestText = readFileSync(join(outDir, "manifest.json"));
  const manifest = JSON.parse(manifestText);

  assert.deepEqual(validateDispatch(dispatch).errors, []);
  assert.deepEqual(validateManifest(manifest).errors, []);

  for (const key of ["eval_run_id", "shard_id", "attempt", "dispatch_key"]) {
    assert.equal(dispatch[key], manifest[key], `manifest.${key} 与 dispatch 不一致`);
  }
  const digest = `sha256:${createHash("sha256").update(manifestText).digest("hex")}`;
  assert.equal(dispatch.manifest_digest, digest);
  assert.ok(dispatch.manifest_url.startsWith("file://"), "manifest_url 应为本地文件 URI");
  assert.ok(Date.parse(dispatch.expires_at) > Date.now(), "dispatch 未过期");

  const token = readFileSync(join(outDir, "task-token"), "utf8").trim();
  const claims = verifyToken(token, {
    secret: TASK_SECRET,
    eval_run_id: dispatch.eval_run_id,
    shard_id: dispatch.shard_id,
    attempt: dispatch.attempt,
  });
  assert.equal(claims.eval_run_id, dispatch.eval_run_id);

  assert.ok(existsSync(join(outDir, "summary.json")), "应输出 summary 便于冒烟报告取数");
});

test("default callback base points at the local platform API", (t) => {
  const outDir = mkdtempSync(join(tmpdir(), "eval-dispatch-"));
  t.after(() => rmSync(outDir, { recursive: true, force: true }));

  makeDispatch(outDir);
  const manifest = JSON.parse(readFileSync(join(outDir, "manifest.json"), "utf8"));
  assert.equal(manifest.callback_base_url, "http://127.0.0.1:8000");
  assert.equal(manifest.cases[0].case_run_id, "ecr_m204");
});

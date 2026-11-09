import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { createScenario, repoRoot, runNode, sha256 } from "./scenario-helpers.mjs";

const script = join(repoRoot, "runner", "scripts", "plan-cases.mjs");

const planArgs = (ctx, extra = []) => ["--dispatch", ctx.dispatchPath, "--state-dir", ctx.stateDir, ...extra];

const postRequests = (ctx) => ctx.platform.requests.filter((request) => request.method !== "GET");

function parseError(stderr) {
  const parsed = JSON.parse(stderr);
  assert.equal(parsed.status, "rejected");
  return parsed;
}

test("plan-cases emits the exact single-line matrix in manifest order", async () => {
  const ctx = await createScenario({
    cases: [
      { case_run_id: "ecr_a", case_key: "case-a", dataset_case_id: "case-a" },
      { case_run_id: "ecr_b", case_key: "case-b", dataset_case_id: "case-b" },
    ],
  });
  try {
    const result = await runNode(script, planArgs(ctx));
    assert.equal(result.code, 0, result.stderr);
    const expected = JSON.stringify([{ id: "ecr_a" }, { id: "ecr_b" }]);
    assert.equal(result.stdout, `${expected}\n`);
    assert.equal(result.stdout.trim().split("\n").length, 1, "matrix must be a single line");
    assert.equal(result.stderr, "");
    // Prepare never claims cases and never calls the platform: only the
    // manifest GET happens, and no state is written.
    assert.deepEqual(postRequests(ctx), []);
    assert.equal(ctx.platform.objects.size, 0);
    assert.equal(existsSync(ctx.stateDir), false, "prepare must not create claims");
  } finally {
    await ctx.close();
  }
});

test("plan-cases writes the matrix to --out instead of stdout", async () => {
  const ctx = await createScenario();
  const outPath = join(ctx.dir, "matrix.json");
  try {
    const result = await runNode(script, planArgs(ctx, ["--out", outPath]));
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stdout, "");
    assert.equal(readFileSync(outPath, "utf8"), `${JSON.stringify([{ id: "ecr_a" }, { id: "ecr_b" }])}\n`);
  } finally {
    await ctx.close();
  }
});

test("plan-cases reads a file:// manifest without touching the network", async () => {
  const ctx = await createScenario();
  const manifestPath = join(ctx.dir, "manifest.json");
  const manifestText = `${JSON.stringify(ctx.manifest, null, 2)}\n`;
  const dispatchPath = join(ctx.dir, "dispatch-file.json");
  try {
    writeFileSync(manifestPath, manifestText);
    writeFileSync(dispatchPath, `${JSON.stringify({
      ...ctx.dispatch,
      manifest_url: `file://${manifestPath}`,
      manifest_digest: sha256(manifestText),
    }, null, 2)}\n`);
    const result = await runNode(script, ["--dispatch", dispatchPath, "--state-dir", ctx.stateDir]);
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stdout, `${JSON.stringify([{ id: "ecr_a" }, { id: "ecr_b" }])}\n`);
    assert.equal(ctx.platform.requests.length, 0, "file:// manifest must not call the platform");
  } finally {
    await ctx.close();
  }
});

test("plan-cases rejects a manifest digest mismatch with a JSON error", async () => {
  const ctx = await createScenario({ manifestDigest: sha256("tampered") });
  try {
    const result = await runNode(script, planArgs(ctx));
    assert.equal(result.code, 2);
    assert.equal(parseError(result.stderr).error_code, "manifest_digest_mismatch");
    assert.equal(result.stdout, "");
    assert.deepEqual(postRequests(ctx), []);
  } finally {
    await ctx.close();
  }
});

test("plan-cases rejects an expired dispatch with a JSON error", async () => {
  const ctx = await createScenario({ expiresInMs: -60_000 });
  try {
    const result = await runNode(script, planArgs(ctx));
    assert.equal(result.code, 2);
    assert.equal(parseError(result.stderr).error_code, "token_invalid");
    assert.equal(result.stdout, "");
    assert.deepEqual(postRequests(ctx), []);
  } finally {
    await ctx.close();
  }
});

test("plan-cases rejects a manifest that does not match the dispatch", async () => {
  const ctx = await createScenario({ dispatchOverrides: { dispatch_key: "other:shard:1" } });
  try {
    const result = await runNode(script, planArgs(ctx));
    assert.equal(result.code, 2);
    assert.equal(parseError(result.stderr).error_code, "attempt_mismatch");
    assert.equal(result.stdout, "");
  } finally {
    await ctx.close();
  }
});

test("plan-cases rejects an invalid dispatch before fetching the manifest", async () => {
  const ctx = await createScenario({ dispatchOverrides: { manifest_digest: "" } });
  try {
    const result = await runNode(script, planArgs(ctx));
    assert.equal(result.code, 2);
    assert.equal(parseError(result.stderr).error_code, "manifest_invalid");
    assert.equal(ctx.platform.requests.length, 0, "invalid dispatch must not touch the platform");
  } finally {
    await ctx.close();
  }
});

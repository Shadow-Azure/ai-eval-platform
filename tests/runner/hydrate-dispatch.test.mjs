import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const script = join(repoRoot, "runner", "scripts", "hydrate-dispatch.mjs");
const runFile = promisify(execFile);
const sha256 = (bytes) => `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
const future = (ms = 3_600_000) => new Date(Date.now() + ms).toISOString();

function dispatchFor(manifestUrl) {
  return {
    schema_version: "1.1",
    eval_run_id: "run-1",
    shard_id: "shard_000",
    attempt: 1,
    dispatch_key: "run-1:shard_000:1",
    manifest_url: manifestUrl,
    runs_on: ["self-hosted", "linux", "eval"],
    job_timeout_minutes: 70,
  };
}

async function withServer(handler, fn) {
  const server = createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  try {
    return await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

async function runHydrate(dispatch, extra = []) {
  const dir = mkdtempSync(join(tmpdir(), "m8-51-hydrate-"));
  const path = join(dir, "dispatch.json");
  writeFileSync(path, `${JSON.stringify(dispatch, null, 2)}\n`);
  try {
    const { stdout, stderr } = await runFile(
      process.execPath,
      [script, "--dispatch", path, "--out", path, ...extra],
      { timeout: 15000 },
    );
    return { ok: true, stdout, stderr, path, merged: JSON.parse(readFileSync(path, "utf8")) };
  } catch (error) {
    return { ok: false, stdout: error.stdout ?? "", stderr: error.stderr ?? "", path };
  }
}

function manifestHandler(manifestText, { token = "task-token", descriptor } = {}) {
  return (request, response) => {
    if (request.url.startsWith("/manifest")) {
      response.writeHead(200, {
        "content-type": "application/json",
        ...(token ? { "x-runner-task-token": token } : {}),
      });
      response.end(manifestText);
      return;
    }
    if (request.url.startsWith("/descriptor")) {
      if (request.headers.authorization !== `Bearer ${token}`) {
        response.writeHead(401);
        response.end("bad token");
        return;
      }
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify(descriptor));
      return;
    }
    response.writeHead(404);
    response.end();
  };
}

test("an 1.1 dispatch is hydrated from the manifest and descriptor", async () => {
  const manifest = JSON.stringify({
    schema_version: "1.0",
    eval_run_id: "run-1",
    shard_id: "shard_000",
    attempt: 1,
    dispatch_key: "run-1:shard_000:1",
    callback_base_url: "http://platform",
    deadline: future(),
    case_timeout_seconds: 3600,
    max_attempts: 2,
    cases: [],
  });
  const bundle = {
    name: "eval-runner-scripts-0123456789abcdef0123456789abcdef.tar.gz",
    md5: "0".repeat(32),
    sha256: "a".repeat(64),
    size: 40960,
  };
  const digest = sha256(Buffer.from(manifest));
  const expiresAt = future(7_200_000);
  await withServer(
    manifestHandler(manifest, {
      descriptor: {
        schema_version: "1.0",
        manifest_digest: digest,
        expires_at: expiresAt,
        scripts_bundle: bundle,
      },
    }),
    async (base) => {
      const result = await runHydrate(dispatchFor(`${base}/manifest?x=1`));
      assert.equal(result.ok, true, result.stderr);
      assert.equal(result.merged.schema_version, "1.1");
      assert.equal(result.merged.manifest_digest, digest);
      assert.equal(result.merged.expires_at, expiresAt);
      // The bundle pin is served by /scripts; the hydrated dispatch stays at
      // 10 properties (8 start fields + digest + expiry).
      assert.equal(result.merged.scripts_bundle, undefined);
      assert.equal(Object.keys(result.merged).length, 10);
      assert.equal(result.merged.runs_on[0], "self-hosted");
    },
  );
});

test("a descriptor digest that disagrees with the manifest bytes is rejected", async () => {
  const manifest = JSON.stringify({ cases: [] });
  await withServer(
    manifestHandler(manifest, {
      descriptor: {
        schema_version: "1.0",
        manifest_digest: `sha256:${"b".repeat(64)}`,
        expires_at: future(),
        scripts_bundle: null,
      },
    }),
    async (base) => {
      const result = await runHydrate(dispatchFor(`${base}/manifest`));
      assert.equal(result.ok, false);
      assert.match(result.stderr, /manifest_digest_mismatch/);
    },
  );
});

test("a manifest without a task token cannot hydrate", async () => {
  const manifest = JSON.stringify({ cases: [] });
  await withServer(
    manifestHandler(manifest, { token: "", descriptor: {} }),
    async (base) => {
      const result = await runHydrate(dispatchFor(`${base}/manifest`));
      assert.equal(result.ok, false);
      assert.match(result.stderr, /descriptor_token_missing/);
    },
  );
});

test("a 1.0 dispatch passes through untouched", async () => {
  const legacy = {
    ...dispatchFor("http://unused/manifest"),
    schema_version: "1.0",
    manifest_digest: `sha256:${"a".repeat(64)}`,
    expires_at: future(),
  };
  const result = await runHydrate(legacy);
  assert.equal(result.ok, true, result.stderr);
  assert.deepEqual(result.merged, legacy);
});

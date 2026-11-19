/**
 * M8-47 (#312): the workflow's bootstrap fetcher is intentionally inline
 * (there is no checkout yet), so this test extracts the exact embedded script
 * and runs it against a local HTTP server. The scenarios mirror the issue:
 * image cache first, direct OSS second, platform proxy third, bounded failure
 * when no source is reachable.
 */
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const workflowPath = join(repoRoot, ".github", "workflows", "eval-shard.yml");
const runFile = promisify(execFile);

const snippetPattern = /<<'NODE'\n([\s\S]*?)\n {10}NODE/g;

function extractedScripts() {
  const text = readFileSync(workflowPath, "utf8");
  return [...text.matchAll(snippetPattern)]
    .map((match) => match[1].replace(/^ {10}/gm, ""))
    .filter((script) => script.includes("readImageCache()"));
}

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), "m8-47-fetch-"));
  const bundle = Buffer.from("m8-47-fake-scripts-bundle-".repeat(64));
  const sha256 = createHash("sha256").update(bundle).digest("hex");
  const md5 = createHash("md5").update(bundle).digest("hex");
  const name = `eval-runner-scripts-${md5}.tar.gz`;
  return { dir, bundle, sha256, md5, name };
}

async function runFetcher({ dispatch, cacheDir, budgetMs = 3000 }) {
  const scripts = extractedScripts();
  assert.ok(scripts.length >= 1);
  const scriptPath = join(dirname(dispatch), "fetch-bundle.cjs");
  writeFileSync(scriptPath, scripts[0]);
  const archive = join(dirname(dispatch), "eval-scripts.tar.gz");
  const started = Date.now();
  try {
    const { stdout, stderr } = await runFile(
      process.execPath,
      [scriptPath, dispatch, archive],
      {
        env: {
          ...process.env,
          RUNNER_SCRIPTS_IMAGE_DIR: cacheDir,
          RUNNER_SCRIPTS_FETCH_BUDGET_MS: String(budgetMs),
        },
        timeout: 10000,
      },
    );
    return { ok: true, stdout, stderr, archive, elapsed: Date.now() - started };
  } catch (error) {
    return {
      ok: false,
      stdout: error.stdout ?? "",
      stderr: error.stderr ?? "",
      archive,
      elapsed: Date.now() - started,
    };
  }
}

function writeDispatch(root, manifestUrl, pin) {
  const path = join(root, "dispatch.json");
  writeFileSync(
    path,
    JSON.stringify({
      schema_version: "1.0",
      eval_run_id: "eval-m8-47",
      shard_id: "shard_m8_47",
      attempt: 1,
      dispatch_key: "eval-m8-47:shard_m8_47:1",
      manifest_url: manifestUrl,
      manifest_digest: `sha256:${"0".repeat(64)}`,
      expires_at: "2026-11-19T12:00:00Z",
      ...(pin ? { scripts_bundle: pin } : {}),
    }),
  );
  return path;
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

test("workflow embeds the same self-contained fetcher in prepare and execute", () => {
  const scripts = extractedScripts();
  assert.equal(scripts.length, 2, "prepare and execute each embed one fetcher");
  assert.equal(scripts[0], scripts[1], "both jobs must use the same acquisition logic");
  for (const expected of [
    "RUNNER_SCRIPTS_FETCH_BUDGET_MS",
    "readImageCache()",
    "metadata.oss_url",
    'source = "image"',
  ]) {
    assert.ok(scripts[0].includes(expected), `fetcher missing ${expected}`);
  }
});

test("image cache is used without calling the platform scripts endpoint", async () => {
  const { dir, bundle, sha256, md5, name } = fixture();
  const cacheDir = join(dir, "image-cache");
  mkdirSync(cacheDir);
  writeFileSync(join(cacheDir, name), bundle);
  let requests = 0;
  await withServer(
    (_request, response) => {
      requests += 1;
      response.writeHead(503);
      response.end("platform endpoint is intentionally down");
    },
    async (base) => {
      const dispatch = writeDispatch(dir, `${base}/manifest`, {
        name,
        md5,
        sha256,
        size: bundle.length,
      });
      const result = await runFetcher({ dispatch, cacheDir });
      assert.equal(result.ok, true, result.stderr);
      assert.match(result.stdout, /source=image/);
      assert.equal(requests, 0, "a valid image cache must not touch the network");
      assert.equal(readFileSync(result.archive).length, bundle.length);
    },
  );
});

test("metadata names the bundle so a pre-seeded cache survives a payload without a pin", async () => {
  const { dir, bundle, sha256, md5, name } = fixture();
  const cacheDir = join(dir, "image-cache");
  mkdirSync(cacheDir);
  writeFileSync(join(cacheDir, name), bundle);
  let bundleDownloads = 0;
  await withServer(
    (request, response) => {
      if (request.url.startsWith("/scripts")) {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(
          JSON.stringify({
            name,
            md5,
            sha256,
            size: bundle.length,
            token: "short-lived-scripts-token",
            url: `http://127.0.0.1:${request.socket.localPort}/proxy`,
            oss_url: `http://127.0.0.1:${request.socket.localPort}/oss`,
          }),
        );
      } else {
        bundleDownloads += 1;
        response.writeHead(200);
        response.end(bundle);
      }
    },
    async (base) => {
      // M8-51 (#323): production dispatches no longer carry the pin, so the
      // /scripts metadata response must unlock the pre-seeded local cache.
      const dispatch = writeDispatch(dir, `${base}/manifest`, null);
      const result = await runFetcher({ dispatch, cacheDir });
      assert.equal(result.ok, true, result.stderr);
      assert.match(result.stdout, /source=image/);
      assert.equal(bundleDownloads, 0, "a verified cache hit must not download the bundle");
      assert.equal(readFileSync(result.archive).length, bundle.length);
    },
  );
});

test("direct private OSS presigned URL is preferred over the platform proxy", async () => {
  const { dir, bundle, sha256, md5, name } = fixture();
  const cacheDir = join(dir, "empty-cache");
  mkdirSync(cacheDir);
  let proxyHits = 0;
  await withServer(
    (request, response) => {
      if (request.url.startsWith("/manifest")) {
        response.writeHead(404);
        response.end();
      } else if (request.url.startsWith("/scripts")) {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(
          JSON.stringify({
            name,
            md5,
            sha256,
            size: bundle.length,
            token: "short-lived-scripts-token",
            url: `http://127.0.0.1:${request.socket.localPort}/proxy`,
            oss_url: `http://127.0.0.1:${request.socket.localPort}/oss`,
          }),
        );
      } else if (request.url === "/oss") {
        response.writeHead(200);
        response.end(bundle);
      } else {
        proxyHits += 1;
        response.writeHead(500);
        response.end("proxy should not be used");
      }
    },
    async (base) => {
      const dispatch = writeDispatch(dir, `${base}/manifest`, {
        name,
        md5,
        sha256,
        size: bundle.length,
      });
      const result = await runFetcher({ dispatch, cacheDir });
      assert.equal(result.ok, true, result.stderr);
      assert.match(result.stdout, /source=oss/);
      assert.equal(proxyHits, 0, "direct OSS must win before the platform proxy");
    },
  );
});

test("platform proxy is the network fallback when direct OSS fails", async () => {
  const { dir, bundle, sha256, md5, name } = fixture();
  const cacheDir = join(dir, "empty-cache");
  mkdirSync(cacheDir);
  await withServer(
    (request, response) => {
      if (request.url.startsWith("/scripts")) {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(
          JSON.stringify({
            name,
            md5,
            sha256,
            size: bundle.length,
            token: "short-lived-scripts-token",
            url: `http://127.0.0.1:${request.socket.localPort}/proxy`,
            oss_url: `http://127.0.0.1:${request.socket.localPort}/oss`,
          }),
        );
      } else if (request.url === "/oss") {
        response.writeHead(500);
        response.end("oss path down");
      } else if (request.url === "/proxy") {
        assert.equal(request.headers.authorization, "Bearer short-lived-scripts-token");
        response.writeHead(200);
        response.end(bundle);
      } else {
        response.writeHead(404);
        response.end();
      }
    },
    async (base) => {
      const dispatch = writeDispatch(dir, `${base}/manifest`, {
        name,
        md5,
        sha256,
        size: bundle.length,
      });
      const result = await runFetcher({ dispatch, cacheDir });
      assert.equal(result.ok, true, result.stderr);
      assert.match(result.stdout, /source=proxy/);
    },
  );
});

test("a corrupt image cache is rejected and never handed to extraction", async () => {
  const { dir, bundle, sha256, md5, name } = fixture();
  const cacheDir = join(dir, "bad-cache");
  mkdirSync(cacheDir);
  writeFileSync(join(cacheDir, name), Buffer.from("corrupt bytes"));
  await withServer(
    (request, response) => {
      if (request.url.startsWith("/scripts")) {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(
          JSON.stringify({
            name,
            md5,
            sha256,
            size: bundle.length,
            token: "short-lived-scripts-token",
            url: `http://127.0.0.1:${request.socket.localPort}/proxy`,
            oss_url: null,
          }),
        );
      } else if (request.url === "/proxy") {
        response.writeHead(200);
        response.end(bundle);
      } else {
        response.writeHead(404);
        response.end();
      }
    },
    async (base) => {
      const dispatch = writeDispatch(dir, `${base}/manifest`, {
        name,
        md5,
        sha256,
        size: bundle.length,
      });
      const result = await runFetcher({ dispatch, cacheDir });
      assert.equal(result.ok, true, result.stderr);
      assert.match(result.stdout, /source=proxy/);
    },
  );
});

test("all sources unavailable fails inside the short budget", async () => {
  const { dir, sha256, md5, name } = fixture();
  const cacheDir = join(dir, "empty-cache");
  mkdirSync(cacheDir);
  await withServer(
    (_request, response) => {
      response.writeHead(503);
      response.end("down");
    },
    async (base) => {
      const dispatch = writeDispatch(dir, `${base}/manifest`, {
        name,
        md5,
        sha256,
        size: 512,
      });
      const result = await runFetcher({ dispatch, cacheDir, budgetMs: 1000 });
      assert.equal(result.ok, false, "all sources down must fail");
      assert.match(result.stderr, /scripts bundle unavailable/);
      assert.ok(result.elapsed < 5000, `bounded failure took ${result.elapsed}ms`);
    },
  );
});

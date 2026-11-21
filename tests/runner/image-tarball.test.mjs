import assert from "node:assert/strict";
import { spawn as nodeSpawn } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { loadImageTarball } from "../../runner/lib/image-tarball.mjs";

const TARBALL = {
  url: "https://bucket.oss-cn-hangzhou-internal.aliyuncs.com/dataset-images/x.tar.gz?X-Amz-Signature=secret",
  key: "dataset-images/x.tar.gz",
  image: "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest",
  format: "tar.gz",
};

/**
 * Fake command runner: records each spawn call and returns a real child
 * process that emulates the requested exit code/output. Using a real child
 * exercises `run-command.mjs`'s stream, timeout and close handling instead of
 * bypassing it with a pre-baked result object.
 */
function fakeSpawn(results) {
  const calls = [];
  const spawn = (command, args, options) => {
    calls.push({ command, args, options });
    const result = results.shift() ?? { status: 0 };
    const script = [];
    if (result.stdout) script.push(`process.stdout.write(${JSON.stringify(result.stdout)});`);
    if (result.stderr) script.push(`process.stderr.write(${JSON.stringify(result.stderr)});`);
    if (result.delayMs) {
      script.push(`setTimeout(() => { process.exitCode = ${result.status ?? 0}; }, ${result.delayMs});`);
    } else {
      script.push(`process.exitCode = ${result.status ?? 0};`);
    }
    return nodeSpawn(process.execPath, ["-e", script.join("\n")], {
      ...options,
      stdio: ["ignore", "pipe", "pipe"],
    });
  };
  return { spawn, calls };
}

function traceDir() {
  return mkdtempSync(join(tmpdir(), "image-tarball-test-"));
}

test("downloads, loads and verifies an image tarball", async () => {
  const dir = traceDir();
  const { spawn, calls } = fakeSpawn([{ status: 1 }]);
  const result = await loadImageTarball(TARBALL, { traceDir: dir, spawn, downloadTimeoutMs: 1000 });
  assert.equal(result.ok, true);
  assert.equal(result.stage, "loaded");
  assert.equal(result.image, TARBALL.image);
  assert.deepEqual(
    calls.map((call) => [call.command, call.args[0]]),
    [
      ["docker", "image"],
      ["curl", "-fsSL"],
      ["docker", "load"],
      ["docker", "image"],
    ],
  );
  const trace = readFileSync(join(dir, "image-tarball.json"), "utf8");
  assert.match(trace, /"status"|"ok": true/);
  assert.doesNotMatch(trace, /X-Amz-Signature|secret/);
});

test("an already cached image skips the download", async () => {
  const { spawn, calls } = fakeSpawn([{ status: 0 }]);
  const result = await loadImageTarball(TARBALL, { spawn });
  assert.equal(result.ok, true);
  assert.equal(result.stage, "cached");
  assert.equal(calls.length, 1);
});

test("download failure reports the stage and does not load", async () => {
  const { spawn, calls } = fakeSpawn([{ status: 1 }, { status: 22, stdout: "", stderr: "HTTP 404" }]);
  const result = await loadImageTarball(TARBALL, { spawn });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "download");
  assert.match(result.error, /404/);
  assert.equal(calls.length, 2);
});

test("download timeout reports the stage and is bounded", async () => {
  const { spawn } = fakeSpawn([{ status: 1 }, { status: 0, delayMs: 5000 }]);
  const startedAt = Date.now();
  const result = await loadImageTarball(TARBALL, { spawn, downloadTimeoutMs: 100 });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "download");
  assert.match(result.error, /超时/);
  assert.ok(Date.now() - startedAt < 3000, "download timeout should terminate the child");
});

test("load failure reports the stage without inspecting the image", async () => {
  const { spawn, calls } = fakeSpawn([
    { status: 1 },
    { status: 0 },
    { status: 1, stderr: "invalid tar" },
  ]);
  const result = await loadImageTarball(TARBALL, { spawn });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "load");
  assert.match(result.error, /invalid tar/);
  assert.equal(calls.length, 3);
});

test("missing image after load fails verification", async () => {
  const { spawn } = fakeSpawn([
    { status: 1 },
    { status: 0 },
    { status: 0 },
    { status: 1, stderr: "No such image" },
  ]);
  const result = await loadImageTarball(TARBALL, { spawn });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "verify");
});

test("invalid or missing URL is rejected before any command", async () => {
  const { spawn, calls } = fakeSpawn([]);
  const result = await loadImageTarball({ ...TARBALL, url: "file:///tmp/x.tar.gz" }, { spawn });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "invalid");
  assert.equal(calls.length, 0);
});

test("signed URL never reaches the diagnostics", async () => {
  const dir = traceDir();
  const { spawn } = fakeSpawn([
    { status: 1 },
    { status: 22, stderr: `curl: (22) failed ${TARBALL.url}` },
  ]);
  const result = await loadImageTarball(TARBALL, { traceDir: dir, spawn });
  assert.equal(result.ok, false);
  assert.doesNotMatch(result.error, /X-Amz-Signature|secret/);
  assert.doesNotMatch(readFileSync(join(dir, "image-tarball.json"), "utf8"), /X-Amz-Signature|secret/);
});

test("image loading keeps timers responsive while a command runs", async () => {
  const { spawn } = fakeSpawn([
    { status: 1 },
    { status: 0, delayMs: 400 },
    { status: 0 },
    { status: 0 },
  ]);
  const order = [];
  const timer = new Promise((resolve) => {
    setTimeout(() => {
      order.push("timer");
      resolve();
    }, 50);
  });
  const loading = loadImageTarball(TARBALL, { spawn }).then((result) => {
    order.push("load");
    return result;
  });
  const [result] = await Promise.all([loading, timer]);
  assert.equal(result.ok, true);
  assert.deepEqual(order, ["timer", "load"]);
});

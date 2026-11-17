import assert from "node:assert/strict";
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

function fakeSpawn(results) {
  const calls = [];
  const spawn = (command, args, options) => {
    calls.push({ command, args, options });
    return results.shift() ?? { status: 0, stdout: "", stderr: "" };
  };
  return { spawn, calls };
}

function traceDir() {
  return mkdtempSync(join(tmpdir(), "image-tarball-test-"));
}

test("downloads, loads and verifies an image tarball", () => {
  const dir = traceDir();
  const { spawn, calls } = fakeSpawn([{ status: 1 }]);
  const result = loadImageTarball(TARBALL, { traceDir: dir, spawn, downloadTimeoutMs: 1000 });
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

test("an already cached image skips the download", () => {
  const { spawn, calls } = fakeSpawn([{ status: 0 }]);
  const result = loadImageTarball(TARBALL, { spawn });
  assert.equal(result.ok, true);
  assert.equal(result.stage, "cached");
  assert.equal(calls.length, 1);
});

test("download failure reports the stage and does not load", () => {
  const { spawn, calls } = fakeSpawn([{ status: 1 }, { status: 22, stdout: "", stderr: "HTTP 404" }]);
  const result = loadImageTarball(TARBALL, { spawn });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "download");
  assert.match(result.error, /404/);
  assert.equal(calls.length, 2);
});

test("load failure reports the stage without inspecting the image", () => {
  const { spawn, calls } = fakeSpawn([
    { status: 1 },
    { status: 0 },
    { status: 1, stderr: "invalid tar" },
  ]);
  const result = loadImageTarball(TARBALL, { spawn });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "load");
  assert.match(result.error, /invalid tar/);
  assert.equal(calls.length, 3);
});

test("missing image after load fails verification", () => {
  const { spawn } = fakeSpawn([
    { status: 1 },
    { status: 0 },
    { status: 0 },
    { status: 1, stderr: "No such image" },
  ]);
  const result = loadImageTarball(TARBALL, { spawn });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "verify");
});

test("invalid or missing URL is rejected before any command", () => {
  const { spawn, calls } = fakeSpawn([]);
  const result = loadImageTarball({ ...TARBALL, url: "file:///tmp/x.tar.gz" }, { spawn });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "invalid");
  assert.equal(calls.length, 0);
});

test("signed URL never reaches the diagnostics", () => {
  const dir = traceDir();
  const { spawn } = fakeSpawn([
    { status: 1 },
    { status: 22, stderr: `curl: (22) failed ${TARBALL.url}` },
  ]);
  const result = loadImageTarball(TARBALL, { traceDir: dir, spawn });
  assert.equal(result.ok, false);
  assert.doesNotMatch(result.error, /X-Amz-Signature|secret/);
  assert.doesNotMatch(readFileSync(join(dir, "image-tarball.json"), "utf8"), /X-Amz-Signature|secret/);
});

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const writer = join(root, "deploy", "write-managed-env.sh");
const secrets = {
  SECRET_BOX_KEY: "secret-box",
  POSTGRES_PASSWORD: "postgres",
  AUTH_SESSION_SECRET: "auth-session",
  GATEWAY_TOKEN_SECRET: "gateway-token",
  RUNNER_TASK_SECRET: "runner-task",
  RUNNER_HEARTBEAT_TOKEN: "runner-heartbeat",
  RUNNER_ADMIN_TOKEN: "runner-admin",
};

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function hashLines() {
  return Object.entries(secrets)
    .map(([name, value]) => `${name} ${digest(value)}`)
    .join("\n");
}

function payload() {
  return [
    "S3_ENDPOINT=https://oss.example",
    "S3_BUCKET=private",
    ...Object.entries(secrets).map(([name, value]) => `${name}=${value}`),
    "",
  ].join("\n");
}

function runWriter({ envContent, existing = true, body = payload(), expectedDigest = digest(body) }) {
  const dir = mkdtempSync(join(tmpdir(), "managed-env-writer-"));
  const envFile = join(dir, ".env");
  if (existing) writeFileSync(envFile, envContent ?? "");
  const input = `${hashLines()}\n--payload--\n${body}`;
  const result = spawnSync("bash", [writer, expectedDigest], {
    input,
    encoding: "utf8",
    env: { ...process.env, ENV_FILE: envFile },
  });
  const after = existing || result.status === 0 ? readFileSync(envFile, "utf8") : null;
  const mode = existsSync(envFile) ? (statSync(envFile).mode & 0o777).toString(8) : null;
  rmSync(dir, { recursive: true, force: true });
  return { result, after, mode };
}

test("writes a complete env atomically and preserves unmanaged keys", () => {
  const existing = [
    "UNMANAGED_SETTING=kept",
    ...Object.entries(secrets).map(([name, value]) => `${name}=${value}`),
    "S3_ENDPOINT=https://old.example",
    "",
  ].join("\n");
  const { result, after, mode } = runWriter({ envContent: existing });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(mode, "600");
  assert.match(after, /^UNMANAGED_SETTING=kept$/m);
  assert.match(after, /^S3_ENDPOINT=https:\/\/oss\.example$/m);
  assert.equal((after.match(/^S3_ENDPOINT=/gm) ?? []).length, 1);
});

test("initializes when the host env does not exist", () => {
  const { result, after, mode } = runWriter({ existing: false });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(mode, "600");
  assert.match(after, /^SECRET_BOX_KEY=secret-box$/m);
});

test("initializes from an empty pre-created template", () => {
  const { result, after } = runWriter({ existing: true, envContent: "" });
  assert.equal(result.status, 0, result.stderr);
  assert.match(after, /^RUNNER_ADMIN_TOKEN=runner-admin$/m);
});

test("preserves unmanaged keys when no long-lived key exists yet", () => {
  const { result, after, mode } = runWriter({
    existing: true,
    envContent: "KEEP_ME=template-value\n",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(mode, "600");
  assert.match(after, /^KEEP_ME=template-value$/m);
  assert.match(after, /^SECRET_BOX_KEY=secret-box$/m);
});

test("stages the atomic replacement beside the target env file", () => {
  const script = readFileSync(writer, "utf8");
  assert.ok(
    script.includes('mktemp "${env_file}.XXXXXX"'),
    "the output temp file must share the target directory so rename stays atomic",
  );
});

test("rejects a partially populated host without replacing it", () => {
  const existing = "SECRET_BOX_KEY=wrong\n";
  const { result, after } = runWriter({ existing: true, envContent: existing });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /partial|mismatch/i);
  assert.equal(after, existing);
});

test("rejects a truncated payload without replacing the host env", () => {
  const full = payload();
  const existing = `${Object.entries(secrets).map(([name, value]) => `${name}=${value}`).join("\n")}\n`;
  const { result, after } = runWriter({
    existing: true,
    envContent: existing,
    body: full.slice(0, Math.floor(full.length / 2)),
    expectedDigest: digest(full),
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /digest|checksum|incomplete/i);
  assert.equal(after, existing);
});

test("rejects a wrong digest without replacing the host env", () => {
  const existing = `${Object.entries(secrets).map(([name, value]) => `${name}=${value}`).join("\n")}\n`;
  const { result, after } = runWriter({
    existing: true,
    envContent: existing,
    expectedDigest: "0".repeat(64),
  });
  assert.notEqual(result.status, 0);
  assert.equal(after, existing);
});

test("handles an existing env containing only managed keys", () => {
  const existing = `${Object.entries(secrets).map(([name, value]) => `${name}=${value}`).join("\n")}\n`;
  const { result, after } = runWriter({ existing: true, envContent: existing });
  assert.equal(result.status, 0, result.stderr);
  assert.match(after, /^S3_BUCKET=private$/m);
});

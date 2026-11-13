import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHmac } from "node:crypto";
import { readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

/**
 * M8-14: the public GitHub egress listener authenticates Squid's Basic
 * credentials with a stdlib Python helper. These tests pin the helper's v1
 * short-token contract (shared with backend/app/services/short_token.py) and
 * the two-listener deployment shape.
 */

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const read = (path) => readFileSync(join(root, path), "utf8");
const AUTH_SCRIPT = join(root, "deploy", "proxy", "egress_auth.py");
const SECRET = "test-egress-task-secret";

const b64url = (value) => Buffer.from(value).toString("base64url");

function issueToken(claims, secret = SECRET) {
  const body = b64url(JSON.stringify(claims));
  const signature = createHmac("sha256", secret).update(body).digest("base64url");
  return `v1.${body}.${signature}`;
}

function egressClaims(overrides = {}) {
  return {
    scope: "egress",
    eval_run_id: "run-1",
    shard_id: "shard_000",
    attempt: 1,
    pool_id: "fixed-linux",
    exp: Math.floor(Date.now() / 1000) + 600,
    ...overrides,
  };
}

/** Squid sends "<username> <password>"; the password slot is empty. */
function checkCredentials(lines, { secret = SECRET } = {}) {
  const result = spawnSync(process.env.PYTHON ?? "python3", [AUTH_SCRIPT], {
    input: `${lines.join("\n")}\n`,
    encoding: "utf8",
    env: { ...process.env, RUNNER_TASK_SECRET: secret },
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim().split("\n");
}

test("egress_auth.py accepts a valid egress token", () => {
  const token = issueToken(egressClaims());
  assert.deepEqual(checkCredentials([`${token} `]), ["OK"]);
});

test("egress_auth.py rejects an expired token", () => {
  const token = issueToken(egressClaims({ exp: Math.floor(Date.now() / 1000) - 1 }));
  assert.deepEqual(checkCredentials([`${token} `]), ["ERR"]);
});

test("egress_auth.py rejects a forged token", () => {
  const token = issueToken(egressClaims(), "some-other-secret");
  assert.deepEqual(checkCredentials([`${token} `]), ["ERR"]);
});

test("egress_auth.py rejects a token for another scope", () => {
  const token = issueToken(egressClaims({ scope: "case" }));
  assert.deepEqual(checkCredentials([`${token} `]), ["ERR"]);
});

test("egress_auth.py rejects a tampered body", () => {
  const token = issueToken(egressClaims());
  const [version, , signature] = token.split(".");
  const forgedBody = b64url(JSON.stringify(egressClaims({ eval_run_id: "run-2" })));
  assert.deepEqual(checkCredentials([`${version}.${forgedBody}.${signature} `]), ["ERR"]);
});

test("egress_auth.py fails closed without the shared secret", () => {
  const token = issueToken(egressClaims(), "");
  assert.deepEqual(checkCredentials([`${token} `], { secret: "" }), ["ERR"]);
});

test("egress_auth.py answers every line in order and rejects malformed input", () => {
  const valid = issueToken(egressClaims());
  assert.deepEqual(checkCredentials([`${valid} `, "not-a-token ", `${valid}`]), [
    "OK",
    "ERR",
    "OK",
  ]);
});

test("squid exposes a private and an authenticated public listener", () => {
  const squid = read("deploy/proxy/squid.conf");
  for (const expected of [
    "http_port 3128",
    "http_port 3129 name=public",
    "auth_param basic program /usr/local/bin/egress_auth.py",
    "acl authenticated proxy_auth REQUIRED",
    "acl public_port",
    "http_access allow runner_network !public_port github_domains SSL_ports",
    "http_access allow authenticated public_port github_domains SSL_ports",
    "http_access deny all",
    "dstdomain github.com",
    "access_log stdio:/var/log/squid/access.log squid",
  ]) {
    assert.ok(squid.includes(expected), `squid.conf missing ${expected}`);
  }
});

test("production compose injects the task secret and publishes both listeners", () => {
  const compose = read("deploy/docker-compose.prod.yml");
  const start = compose.indexOf("\n  proxy:");
  assert.ok(start >= 0, "compose must define the proxy service");
  const end = compose.indexOf("\n  migrate:", start);
  assert.ok(end > start, "proxy service must precede migrate");
  const proxy = compose.slice(start, end);
  assert.ok(proxy.includes("RUNNER_TASK_SECRET: ${RUNNER_TASK_SECRET:-}"), "proxy needs the signing secret");
  assert.ok(
    proxy.includes('"${RUNNER_EGRESS_PROXY_BIND_ADDRESS:-0.0.0.0}:3128:3128"'),
    "private listener must be published",
  );
  assert.ok(
    proxy.includes('"${RUNNER_EGRESS_PROXY_PUBLIC_BIND_ADDRESS:-0.0.0.0}:3129:3129"'),
    "public listener must be published",
  );
  assert.ok(
    proxy.includes("./proxy/egress_auth.py:/usr/local/bin/egress_auth.py:ro"),
    "the auth helper must be mounted",
  );
  assert.ok(proxy.includes("./proxy/squid.conf:/etc/squid/squid.conf:ro"));
});

test("the proxy image adds Python for the auth helper", () => {
  const dockerfile = read("deploy/proxy/Dockerfile");
  assert.ok(dockerfile.includes("FROM ubuntu/squid"));
  assert.ok(dockerfile.includes("python3"));
});

test("egress_auth.py is executable and documents the shared wire format", () => {
  const mode = statSync(AUTH_SCRIPT).mode;
  assert.ok((mode & 0o111) !== 0, "egress_auth.py must be executable");
  const helper = read("deploy/proxy/egress_auth.py");
  assert.ok(helper.startsWith("#!/usr/bin/env python3"));
  assert.ok(helper.includes("backend/app/services/short_token.py"));
  assert.ok(helper.includes("RUNNER_TASK_SECRET"));
});

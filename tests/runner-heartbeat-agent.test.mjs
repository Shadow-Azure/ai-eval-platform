import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { buildPayload, parseLabels, sendHeartbeat } from "../runner/agent/heartbeat.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const agentPath = join(here, "..", "runner", "agent", "heartbeat.mjs");

// The vendored agent must stay byte-identical to the file deployed on the
// fixed host (/opt/ai-eval-platform-runner/heartbeat.mjs). Update this pin
// together with a redeploy via infra/runner/bootstrap.sh.
const DEPLOYED_SHA256 = "41417316a1195d70b4d189f1a64ce58425cc2373d53907db4ed588148e504d1c";

test("vendored heartbeat agent matches the deployed checksum", () => {
  const digest = createHash("sha256").update(readFileSync(agentPath)).digest("hex");
  assert.equal(digest, DEPLOYED_SHA256);
});

test("parseLabels defaults to the fixed-runner label set", () => {
  assert.deepEqual(parseLabels(undefined), ["self-hosted", "linux", "eval"]);
  assert.deepEqual(parseLabels(" linux , eval , , self-hosted "), ["linux", "eval", "self-hosted"]);
  assert.deepEqual(parseLabels(""), ["self-hosted", "linux", "eval"]);
});

test("buildPayload reports online without a current attempt", () => {
  const payload = buildPayload({
    RUNNER_ID: "fixed-linux-eval",
    RUNNER_NAME: "fixed-linux-eval",
    RUNNER_LABELS: "self-hosted,linux,eval",
  });
  assert.deepEqual(payload, {
    runner_id: "fixed-linux-eval",
    name: "fixed-linux-eval",
    labels: ["self-hosted", "linux", "eval"],
    status: "online",
    current_case_attempt_id: null,
  });
});

test("buildPayload reports busy with the current case attempt", () => {
  const payload = buildPayload({
    RUNNER_ID: "fixed-linux-eval",
    RUNNER_CURRENT_CASE_ATTEMPT_ID: "ecr_041",
    RUNNER_LABELS: "self-hosted,linux,eval",
  });
  assert.equal(payload.status, "busy");
  assert.equal(payload.current_case_attempt_id, "ecr_041");
});

function startStub(handler) {
  return new Promise((resolve) => {
    const requests = [];
    const server = createServer(async (request, response) => {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const record = {
        method: request.method,
        url: request.url,
        headers: request.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      };
      requests.push(record);
      handler(record, response);
    });
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        requests,
        baseUrl: `http://127.0.0.1:${port}`,
        close: () => server.close(),
      });
    });
  });
}

const AGENT_ENV = {
  PLATFORM_BASE_URL: "",
  RUNNER_HEARTBEAT_TOKEN: "runner-scoped-token",
  RUNNER_ID: "fixed-linux-eval",
  RUNNER_NAME: "fixed-linux-eval",
  RUNNER_LABELS: "self-hosted,linux,eval",
};

test("sendHeartbeat posts the agent contract to /runner/v1/runners/heartbeat", async () => {
  const stub = await startStub((record, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ ok: true, status: "online" }));
  });
  try {
    const result = await sendHeartbeat({ ...AGENT_ENV, PLATFORM_BASE_URL: stub.baseUrl });
    assert.deepEqual(result, { ok: true, status: "online" });

    const record = stub.requests[0];
    assert.equal(record.method, "POST");
    assert.equal(record.url, "/runner/v1/runners/heartbeat");
    assert.equal(record.headers.authorization, "Bearer runner-scoped-token");
    assert.deepEqual(JSON.parse(record.body), buildPayload(AGENT_ENV));
  } finally {
    stub.close();
  }
});

test("sendHeartbeat tolerates a trailing slash in PLATFORM_BASE_URL", async () => {
  const stub = await startStub((_record, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    response.end("{}");
  });
  try {
    await sendHeartbeat({ ...AGENT_ENV, PLATFORM_BASE_URL: `${stub.baseUrl}/` });
    assert.equal(stub.requests[0].url, "/runner/v1/runners/heartbeat");
  } finally {
    stub.close();
  }
});

test("sendHeartbeat throws on non-2xx responses", async () => {
  const stub = await startStub((_record, response) => {
    response.writeHead(401, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: { code: "runner_token_invalid" } }));
  });
  try {
    await assert.rejects(
      () => sendHeartbeat({ ...AGENT_ENV, PLATFORM_BASE_URL: stub.baseUrl }),
      /HTTP 401/,
    );
  } finally {
    stub.close();
  }
});

test("sendHeartbeat requires PLATFORM_BASE_URL and RUNNER_HEARTBEAT_TOKEN", async () => {
  await assert.rejects(
    () => sendHeartbeat({ RUNNER_HEARTBEAT_TOKEN: "t" }),
    /PLATFORM_BASE_URL/,
  );
  await assert.rejects(
    () => sendHeartbeat({ PLATFORM_BASE_URL: "http://127.0.0.1:8000" }),
    /RUNNER_HEARTBEAT_TOKEN/,
  );
});

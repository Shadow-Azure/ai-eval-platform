import assert from "node:assert/strict";
import test from "node:test";

import { buildPayload, parseLabels, sendHeartbeat } from "../../runner/scripts/heartbeat.mjs";

test("parseLabels trims and filters labels", () => {
  assert.deepEqual(parseLabels(" self-hosted, linux ,eval, "), ["self-hosted", "linux", "eval"]);
  assert.deepEqual(parseLabels(undefined), ["self-hosted", "linux", "eval"]);
});

test("buildPayload reports online without an attempt and busy with one", () => {
  const online = buildPayload({ RUNNER_ID: "runner-1", RUNNER_NAME: "fixed", RUNNER_LABELS: "linux,eval" });
  assert.equal(online.runner_id, "runner-1");
  assert.equal(online.status, "online");
  assert.equal(online.current_case_attempt_id, null);
  assert.deepEqual(online.labels, ["linux", "eval"]);

  const busy = buildPayload({
    RUNNER_ID: "runner-1",
    RUNNER_NAME: "fixed",
    RUNNER_CURRENT_CASE_ATTEMPT_ID: "attempt-9",
  });
  assert.equal(busy.status, "busy");
  assert.equal(busy.current_case_attempt_id, "attempt-9");
});

test("sendHeartbeat posts the token and payload to the platform", async () => {
  const calls = [];
  const fakeFetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ status: "ok" }) };
  };
  const result = await sendHeartbeat(
    {
      PLATFORM_BASE_URL: "http://platform.local/",
      RUNNER_HEARTBEAT_TOKEN: "secret-token",
      RUNNER_ID: "runner-1",
      RUNNER_NAME: "fixed",
    },
    fakeFetch,
  );
  assert.equal(result.status, "ok");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://platform.local/runner/v1/runners/heartbeat");
  assert.equal(calls[0].options.headers.authorization, "Bearer secret-token");
  assert.equal(JSON.parse(calls[0].options.body).runner_id, "runner-1");
});

test("sendHeartbeat surfaces missing configuration", async () => {
  await assert.rejects(() => sendHeartbeat({}, async () => ({ ok: true })), /PLATFORM_BASE_URL/);
  await assert.rejects(
    () => sendHeartbeat({ PLATFORM_BASE_URL: "http://platform.local" }, async () => ({ ok: true })),
    /RUNNER_HEARTBEAT_TOKEN/,
  );
});

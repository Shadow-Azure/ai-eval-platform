import assert from "node:assert/strict";
import test from "node:test";

import {
  buildFailureDetail,
  classifyAgentFailure,
  terminalErrorText,
} from "../../runner/lib/failure-classify.mjs";

function streamEvent(record) {
  return `${JSON.stringify(record)}\n`;
}

test("quota exhaustion wins over the generic 429 rate limit", () => {
  const traceText = streamEvent({
    type: "system",
    subtype: "api_error",
    error: {
      type: "rate_limit_error",
      message: "已达到 Token Plan 用量上限：请升级 Token Plan 套餐或购买积分补充用量。(2056)",
    },
    request_id: "req-minimax-2056",
    retryAttempt: 10,
  });
  const failure = classifyAgentFailure({ traceText, exitStatus: 1 });
  assert.equal(failure.code, "model_quota_exhausted");
  assert.equal(failure.retryable, false);
  assert.equal(failure.http_status, 429);
  assert.equal(failure.request_id, "req-minimax-2056");
  assert.match(failure.message, /Token Plan 用量上限/);
  assert.match(failure.message, /配额耗尽/);
});

test("a transient 429 without quota text enters the retry budget", () => {
  const traceText = streamEvent({
    type: "system",
    subtype: "api_error",
    error: { type: "rate_limit_error", message: "rate limited, please retry later" },
  });
  const failure = classifyAgentFailure({ traceText, exitStatus: 1 });
  assert.equal(failure.code, "model_rate_limited");
  assert.equal(failure.retryable, true);
  assert.match(failure.message, /429 限流/);
});

test("HTTP 401 from the gateway is classified as model authentication", () => {
  const failure = classifyAgentFailure({
    stderr: 'gateway error {"error":{"code":"authentication_failed","message":"模型供应商鉴权失败","request_id":"req-auth"}}',
    exitStatus: 1,
  });
  assert.equal(failure.code, "model_authentication_failed");
  assert.equal(failure.retryable, false);
  assert.equal(failure.request_id, "req-auth");
  assert.match(failure.message, /模型鉴权失败/);
});

test("timeout flag and timeout text both become agent_timeout", () => {
  const flagged = classifyAgentFailure({
    traceText: streamEvent({ type: "result", subtype: "error", result: "operation timed out" }),
    exitStatus: null,
    timedOut: true,
  });
  assert.equal(flagged.code, "agent_timeout");
  assert.equal(flagged.retryable, false);
  const textOnly = classifyAgentFailure({ stderr: "ETIMEDOUT connecting to gateway", exitStatus: 1 });
  assert.equal(textOnly.code, "agent_timeout");
});

test("an unknown non-zero exit stays agent_crashed with the stderr tail", () => {
  const failure = classifyAgentFailure({
    traceText: "",
    stderr: "segmentation fault while starting claude",
    exitStatus: 137,
  });
  assert.equal(failure.code, "agent_crashed");
  assert.equal(failure.retryable, false);
  assert.match(failure.message, /exit 137/);
  assert.match(failure.message, /segmentation fault/);
});

test("terminalErrorText reads system/api_error but ignores unrelated records", () => {
  const traceText =
    streamEvent({ type: "assistant", message: { content: [{ type: "text", text: "hello" }] } }) +
    streamEvent({ type: "system", subtype: "api_error", message: "boom 500" });
  const text = terminalErrorText(traceText, "");
  assert.match(text, /boom 500/);
  assert.doesNotMatch(text, /hello/);
});

test("buildFailureDetail keeps setup phase and redacts oversized messages", () => {
  const failure = buildFailureDetail({
    code: "asset_source_install_failed",
    message: "npm error code EEXIST " + "x".repeat(2000),
    phase: "install",
  });
  assert.equal(failure.code, "asset_source_install_failed");
  assert.equal(failure.phase, "install");
  assert.equal(failure.retryable, false);
  assert.ok(failure.message.length <= 1000);
});

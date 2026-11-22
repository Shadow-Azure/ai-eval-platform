import assert from "node:assert/strict";
import test from "node:test";

import { FAILURE_LABELS, failureLabel, failureView } from "../web/src/utils/failure.ts";

test("structured failure renders category, upstream text and request id", () => {
  const view = failureView(
    {
      code: "model_quota_exhausted",
      message: "MiniMax 429 rate_limit_error：已达到 Token Plan 用量上限 (2056)",
      phase: "executing",
      request_id: "req-2056",
      http_status: 429,
      retryable: false,
    },
    "runner_failed",
    "旧摘要",
  );
  assert.equal(view?.label, "配额耗尽");
  assert.match(view?.message ?? "", /Token Plan 用量上限/);
  assert.equal(view?.requestId, "req-2056");
  assert.equal(view?.httpStatus, 429);
  assert.equal(view?.retryable, false);
  assert.equal(view?.tone, "error");
});

test("a retryable 429 gets the warning tone and keeps its label", () => {
  const view = failureView(
    {
      code: "model_rate_limited",
      message: "模型供应商 429 限流：请重试",
      phase: "executing",
      request_id: null,
      http_status: 429,
      retryable: true,
    },
    null,
    null,
  );
  assert.equal(view?.label, "供应商限流");
  assert.equal(view?.tone, "warning");
  assert.equal(view?.retryable, true);
});

test("legacy rows without a structured failure keep their category and cause", () => {
  const view = failureView(null, "asset_source_install_failed", "npm error code EEXIST");
  assert.equal(view?.label, "资产安装失败");
  assert.equal(view?.message, "npm error code EEXIST");
  assert.equal(view?.requestId, null);
  assert.equal(failureView(null, null, null), null);
});

test("every known code has a Chinese label and unknown codes fall back", () => {
  for (const [code, label] of Object.entries(FAILURE_LABELS)) {
    assert.equal(failureLabel(code), label);
    assert.notEqual(label, code);
  }
  assert.equal(failureLabel("unmapped_runner_code"), "unmapped_runner_code");
  assert.equal(failureLabel(null), null);
});

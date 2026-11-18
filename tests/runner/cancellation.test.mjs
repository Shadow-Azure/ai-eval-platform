import assert from "node:assert/strict";
import test from "node:test";

import { buildCancellationFailure } from "../../runner/lib/cancellation.mjs";
import { validatePayload } from "../../runner/lib/contracts.mjs";

const TRACE_MANIFEST = {
  manifest_version: "1.0",
  capture_status: "partial",
  incomplete_reasons: ["runner_cancelled"],
  runtime_version: "2.1.278",
  format_version: "1",
  objects: [
    {
      artifact_kind: "trace",
      logical_path: "claude/stream.jsonl",
      object_key: `evaluations/attempt/${"a".repeat(64)}`,
      sha256: "a".repeat(64),
      size: 128,
      content_type: "application/x-ndjson",
      native_format: "claude-code+jsonl",
      runtime_version: "2.1.278",
      format_version: "1",
    },
  ],
};

const build = (overrides = {}) =>
  buildCancellationFailure({
    caseRunId: "ecr_304",
    attempt: 2,
    eventId: "evt_ecr_304_failed_2",
    sequence: 1_000_001,
    summary: "Runner 收到 SIGTERM，job 在 Agent 执行阶段被取消",
    ...overrides,
  });

test("cancelled job failure is terminal, never retryable and schema-valid", () => {
  for (const [errorCode, traceManifest] of [
    ["runner_lost", null],
    ["runner_lost", TRACE_MANIFEST],
    ["execution_timeout", TRACE_MANIFEST],
  ]) {
    const payload = build({ errorCode, traceManifest });
    const result = validatePayload("failure", payload);
    assert.equal(result.ok, true, result.errors.join("; "));
    assert.equal(payload.error_code, errorCode);
    assert.equal(payload.retryable, false);
    assert.equal(payload.phase, "executing");
    assert.equal(Boolean(payload.trace_manifest), Boolean(traceManifest));
  }
});

test("cancellation summary is bounded and trace manifest is optional", () => {
  const payload = build({ summary: "x".repeat(2000), traceManifest: { objects: [] } });
  assert.equal(payload.error_summary.length, 1000);
  assert.equal("trace_manifest" in payload, false);
  const fallback = build({ summary: "" });
  assert.equal(fallback.error_summary, "job cancelled");
});

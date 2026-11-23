import assert from "node:assert/strict";
import test from "node:test";

import {
  EVENT_TYPE_LABELS,
  clockLabel,
  describeEvent,
  eventSummary,
  recentTimeline,
  TRACE_INGESTION_EVENT,
} from "../web/src/features/evaluations/eventTimeline.ts";

const caseKeys = new Map([
  ["case-a", "django__django-11019"],
  ["case-b", "astropy__astropy-12907"],
]);

let nextId = 1;

function event(eventType, options = {}) {
  const id = options.id ?? nextId++;
  return {
    id,
    event_type: eventType,
    case_run_id: options.caseRunId ?? null,
    attempt_no: options.attempt ?? null,
    sequence: options.sequence ?? 0,
    payload: options.payload ?? {},
    occurred_at: options.occurredAt ?? "2026-11-23T06:48:00Z",
  };
}

function ingestion(options = {}) {
  return event(TRACE_INGESTION_EVENT, {
    ...options,
    payload: {
      fingerprint: "abc",
      trace_status: options.traceStatus ?? "ready",
      attempt_id: "attempt-id",
      file_counts: { expected: 5, pending: 1, parsing: 0, ready: 3, degraded: 0, failed: 0 },
      counts: { expected: 5, pending: 1, parsing: 0, ready: 3, degraded: 0, failed: 0 },
      parser_versions: ["v1"],
      files: [
        { status: "ready", logical_path: "a.jsonl", error_summary: null },
        { status: "ready", logical_path: "b.jsonl", error_summary: null },
        { status: "ready", logical_path: "c.jsonl", error_summary: null },
        { status: "pending", logical_path: "d.jsonl", error_summary: null },
      ],
      errors: [],
      error_summary: null,
    },
  });
}

test("trace.ingestion.updated becomes a counted summary, never a files dump", () => {
  const item = ingestion({ caseRunId: "case-a", attempt: 1 });

  const copy = describeEvent(item, caseKeys);

  assert.equal(copy.subject, "django__django-11019");
  assert.match(copy.action, /^轨迹入库：3 个文件 ready/);
  assert.match(copy.action, /1 个文件 pending/);
  assert.doesNotMatch(copy.action, /files|\[|{/);
  assert.match(copy.context, /轨迹可用/);
  assert.match(copy.context, /attempt 1/);
  assert.match(copy.context, /预期 5 个文件/);
  assert.equal(EVENT_TYPE_LABELS[TRACE_INGESTION_EVENT], "轨迹入库更新");
});

test("full-log summaries stay single-line and never embed raw JSON", () => {
  const item = ingestion({ caseRunId: "case-a", attempt: 2 });
  const summary = eventSummary(item, caseKeys);

  assert.match(summary, /轨迹入库：/);
  assert.doesNotMatch(summary, /django__django-11019/);
  assert.doesNotMatch(summary, /files=|\[\{|"status"/);
});

test("unknown payload objects never leak into the summary", () => {
  const item = event("trace.export.download", {
    caseRunId: "case-a",
    attempt: 1,
    payload: {
      reason: "manual",
      files: [{ logical_path: "a.jsonl", status: "ready" }],
      nested: { huge: true },
    },
  });

  const copy = describeEvent(item, caseKeys);

  assert.equal(copy.action, "trace.export.download");
  assert.match(copy.context, /reason=manual/);
  assert.doesNotMatch(copy.context, /logical_path|huge|\[\{/);
});

test("ingestion failures use the danger tone", () => {
  const item = event(TRACE_INGESTION_EVENT, {
    caseRunId: "case-a",
    attempt: 1,
    payload: {
      trace_status: "failed",
      counts: { expected: 2, ready: 0, pending: 0, failed: 2, degraded: 0 },
      error_summary: "parse error",
    },
  });

  const copy = describeEvent(item, caseKeys);

  assert.equal(copy.tone, "danger");
  assert.match(copy.action, /2 个文件 failed/);
  assert.match(copy.context, /错误：parse error/);
});

test("overview keeps key lifecycle events instead of heartbeats and repeated snapshots", () => {
  const events = [
    event("eval.created", { id: 1, payload: { case_count: 6, shard_count: 2 } }),
    event("case.started", { id: 2, caseRunId: "case-a", attempt: 1 }),
    event("eval.dispatched", { id: 3, payload: { shard_id: "shard-03", github_status: 204, attempt: 1 } }),
    ingestion({ id: 10, caseRunId: "case-a", attempt: 1 }),
    ingestion({ id: 11, caseRunId: "case-a", attempt: 1 }),
    ingestion({ id: 12, caseRunId: "case-a", attempt: 2 }),
    ingestion({ id: 13, caseRunId: "case-b", attempt: 1 }),
    event("case.heartbeat", { id: 14, caseRunId: "case-a", attempt: 1, payload: { sequence: 9 } }),
    event("case.heartbeat", { id: 15, caseRunId: "case-b", attempt: 1, payload: { sequence: 4 } }),
    event("case.completed", {
      id: 16,
      caseRunId: "case-a",
      attempt: 1,
      payload: { result_status: "resolved", trace_status: "ready" },
    }),
    event("case.failed", {
      id: 17,
      caseRunId: "case-b",
      attempt: 1,
      payload: { error_code: "test_failed", error_summary: "2 tests failed", retryable: true },
    }),
  ];

  const timeline = recentTimeline(events, caseKeys);

  assert.equal(timeline.length, 6);
  assert.ok(!timeline.some((item) => item.eventType === "case.heartbeat"));
  const ingestionItems = timeline.filter((item) => item.eventType === TRACE_INGESTION_EVENT);
  assert.equal(ingestionItems.length, 2);
  assert.deepEqual(
    ingestionItems.map((item) => item.id),
    [13, 12],
  );
  const ids = timeline.map((item) => item.id);
  assert.deepEqual(ids, [...ids].sort((left, right) => right - left));
  assert.ok(ids.includes(16));
  assert.ok(ids.includes(17));
  assert.equal(timeline.find((item) => item.id === 16)?.action, "评分完成，已解决");
  assert.equal(timeline.find((item) => item.id === 17)?.tone, "danger");
  assert.equal(timeline.find((item) => item.id === 3)?.subject, "分片 shard-03");
});

test("overview shows only the latest snapshot per attempt when no lifecycle rows exist", () => {
  const events = [
    ingestion({ id: 1, caseRunId: "case-a", attempt: 1 }),
    ingestion({ id: 2, caseRunId: "case-a", attempt: 1 }),
    ingestion({ id: 3, caseRunId: "case-a", attempt: 2 }),
    ingestion({ id: 4, caseRunId: "case-b", attempt: 1 }),
    ingestion({ id: 5, caseRunId: "case-b", attempt: 2 }),
    ingestion({ id: 6, caseRunId: "case-b", attempt: 3 }),
    ingestion({ id: 7, caseRunId: "case-a", attempt: 3 }),
  ];

  const timeline = recentTimeline(events, caseKeys, 4);

  assert.deepEqual(
    timeline.map((item) => item.id),
    [7, 6, 5, 4],
  );
});

test("overview falls back to the newest lifecycle rows when nothing is filtered", () => {
  const events = Array.from({ length: 9 }, (_, index) =>
    event("case.started", {
      id: index + 1,
      caseRunId: index % 2 ? "case-a" : "case-b",
      attempt: index + 1,
    }),
  );

  const timeline = recentTimeline(events, caseKeys, 6);

  assert.deepEqual(
    timeline.map((item) => item.id),
    [9, 8, 7, 6, 5, 4],
  );
});

test("clock labels render as HH:MM and invalid timestamps stay explicit", () => {
  assert.match(clockLabel("2026-11-23T06:48:00Z"), /^\d{2}:\d{2}$/);
  assert.equal(clockLabel(null), "—");
  assert.equal(clockLabel("not-a-date"), "—");
});

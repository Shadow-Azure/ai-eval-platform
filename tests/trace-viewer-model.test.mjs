import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSpanWindows,
  flattenSessionTree,
  mergeSelectedSpan,
  mergeSpanPages,
  markMissingParents,
  readSpanFilter,
  readTraceSelection,
  requestIsCurrent,
  spanBelongsToSession,
  traceStatusForAttempt,
} from "../web/src/features/traces/traceModel.ts";

const sessions = [
  {
    id: "root",
    parent_session_id: null,
    status: "ready",
    ordinal: 0,
    agent_name: "Main agent",
    children: [
      {
        id: "child",
        parent_session_id: "root",
        status: "ready",
        ordinal: 1,
        agent_name: "Reviewer",
        children: [],
      },
    ],
  },
  {
    id: "orphan",
    parent_session_id: "missing",
    status: "unlinked",
    ordinal: 2,
    agent_name: "Orphan agent",
    children: [],
  },
];

test("reads the URL as the source of truth and falls back deterministically", () => {
  assert.deepEqual(readTraceSelection("?attempt=2&session=child&span=span-1"), {
    attempt: 2,
    session: "child",
    span: "span-1",
  });
  assert.deepEqual(readTraceSelection("?attempt=bad&session=&span="), {
    attempt: null,
    session: null,
    span: null,
  });
});

test("span kind filters survive deep links and reject unsupported values", () => {
  assert.equal(readSpanFilter("?kind=TOOL"), "TOOL");
  assert.equal(readSpanFilter("?kind=unknown"), "all");
  assert.equal(readSpanFilter(""), "all");
});

test("flattening exposes linked sessions and an explicit unlinked group", () => {
  const flat = flattenSessionTree(sessions);
  assert.deepEqual(
    flat.linked.map((item) => [item.session.id, item.depth]),
    [
      ["root", 0],
      ["child", 1],
    ],
  );
  assert.deepEqual(
    flat.unlinked.map((item) => item.session.id),
    ["orphan"],
  );
  assert.equal(flat.defaultSessionId, "root");
});

test("merging span pages deduplicates by id and preserves server order", () => {
  const first = [
    { id: "a", ordinal: 1 },
    { id: "b", ordinal: 2 },
  ];
  const merged = mergeSpanPages(first, [
    { id: "b", ordinal: 2, name: "updated" },
    { id: "c", ordinal: 3 },
  ]);
  assert.deepEqual(merged.map((item) => item.id), ["a", "b", "c"]);
  assert.equal(merged[1].name, "updated");
});

test("invalid clocks fall back to ordinal rows while valid clocks keep a time axis", () => {
  const rows = [
    {
      id: "root",
      ordinal: 0,
      kind: "AGENT",
      started_at: "2026-11-12T00:00:00Z",
      ended_at: "2026-11-12T00:01:00Z",
    },
    {
      id: "child",
      ordinal: 1,
      kind: "TOOL",
      started_at: "2026-11-12T00:00:10Z",
      ended_at: "2026-11-12T00:00:20Z",
    },
  ];
  assert.equal(buildSpanWindows(rows, true).mode, "time");
  assert.equal(buildSpanWindows(rows, false).mode, "ordinal");
  assert.equal(buildSpanWindows(rows, true).rows[1].leftPercent, 16.6667);
});

test("a stale request generation can never overwrite the active view", () => {
  assert.equal(requestIsCurrent(3, 3), true);
  assert.equal(requestIsCurrent(2, 3), false);
});

test("missing span parents become explicit root fragments", () => {
  const marked = markMissingParents([
    { id: "root", parent_span_id: null },
    { id: "child", parent_span_id: "root" },
    { id: "fragment", parent_span_id: "missing" },
  ]);
  assert.equal(marked[0].parent_missing, false);
  assert.equal(marked[1].parent_missing, false);
  assert.equal(marked[2].parent_missing, true);
});

test("a deep-link span is accepted only inside the selected session and filter", () => {
  const rootSpan = { id: "root-span", session_id: "root", ordinal: 0, kind: "AGENT" };
  const childSpan = { id: "child-span", session_id: "child", ordinal: 5, kind: "TOOL" };
  assert.equal(spanBelongsToSession(rootSpan, "root"), true);
  assert.equal(spanBelongsToSession(rootSpan, "child"), false);
  assert.equal(spanBelongsToSession(null, "root"), false);

  const merged = mergeSelectedSpan(
    [{ id: "loaded", session_id: "root", ordinal: 1, kind: "LLM" }],
    rootSpan,
    "root",
    "all",
  );
  assert.deepEqual(merged.map((span) => span.id), ["root-span", "loaded"]);
  assert.equal(mergeSelectedSpan([], childSpan, "root", "all").length, 0);
  assert.equal(mergeSelectedSpan([], rootSpan, "root", "TOOL").length, 0);
});

test("attempt trace status never inherits an unrelated case status", () => {
  assert.equal(
    traceStatusForAttempt({
      caseStatus: "ready",
      latestAttempt: true,
      fileStatuses: [],
    }),
    "pending",
  );
  assert.equal(
    traceStatusForAttempt({
      caseStatus: "ready",
      latestAttempt: false,
      fileStatuses: ["ready"],
    }),
    "ready",
  );
  assert.equal(
    traceStatusForAttempt({
      caseStatus: "ready",
      latestAttempt: true,
      fileStatuses: ["ready", "degraded"],
    }),
    "degraded",
  );
});

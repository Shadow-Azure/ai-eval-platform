import assert from "node:assert/strict";
import test from "node:test";

import {
  archiveRootName,
  buildArchiveTree,
  formatBytes,
  parseArchiveFilename,
  summarizeTraceExport,
} from "../web/src/features/traces/traceExportModel.ts";

function session(overrides) {
  return {
    id: "session",
    parent_session_id: null,
    status: "ready",
    ordinal: 0,
    agent_name: null,
    agent_id: null,
    span_count: 0,
    children: [],
    ...overrides,
  };
}

const sessions = [
  session({
    id: "root",
    ordinal: 0,
    agent_name: "Main agent",
    span_count: 17,
    children: [
      session({
        id: "child-b",
        parent_session_id: "root",
        ordinal: 2,
        agent_name: "test_runner",
        span_count: 4,
        children: [
          session({
            id: "grandchild",
            parent_session_id: "child-b",
            ordinal: 0,
            agent_id: "agent_47c9",
            span_count: 2,
          }),
        ],
      }),
      session({
        id: "child-a",
        parent_session_id: "root",
        ordinal: 1,
        agent_id: "agent_a1",
        span_count: 3,
      }),
    ],
  }),
  session({
    id: "orphan",
    parent_session_id: null,
    status: "unlinked",
    ordinal: 1,
    agent_name: "Orphan agent",
    span_count: 1,
  }),
  session({
    id: "lost-parent",
    parent_session_id: "missing",
    ordinal: 2,
    agent_name: "Lost agent",
    span_count: 1,
  }),
];

test("splits main, subagent and unlinked sessions without double counting", () => {
  const summary = summarizeTraceExport(sessions, [
    { size: 1_200_000 },
    { size: 624_000 },
    { size: 0 },
  ]);
  assert.equal(summary.mainAgentCount, 1);
  assert.equal(summary.subagentCount, 3);
  assert.equal(summary.unlinkedCount, 2);
  assert.equal(summary.sessionCount, 6);
  assert.equal(summary.rawFileCount, 3);
  assert.equal(summary.totalSize, 1_824_000);
  assert.equal(summary.estimatedArchiveSize, 1_824_000 + 8192);
  assert.deepEqual(
    summary.sessions.map((row) => [row.id, row.role]),
    [
      ["root", "main"],
      ["child-a", "subagent"],
      ["child-b", "subagent"],
      ["grandchild", "subagent"],
      ["orphan", "unlinked"],
      ["lost-parent", "unlinked"],
    ],
  );
  assert.deepEqual(
    summary.unlinked.map((row) => row.id),
    ["orphan", "lost-parent"],
  );
});

test("session rows carry names, roles, spans and parent links deterministically", () => {
  const summary = summarizeTraceExport(sessions);
  const byId = new Map(summary.sessions.map((row) => [row.id, row]));
  assert.equal(byId.get("root").name, "Main agent");
  assert.equal(byId.get("root").roleLabel, "主 Agent");
  assert.equal(byId.get("child-a").name, "agent_a1");
  assert.equal(byId.get("child-a").parentId, "root");
  assert.equal(byId.get("child-b").name, "test_runner");
  assert.equal(byId.get("child-b").spanCount, 4);
  assert.equal(byId.get("grandchild").depth, 2);
  assert.equal(byId.get("orphan").roleLabel, "未关联");
  assert.equal(byId.get("lost-parent").parentId, "missing");
  // Repeated calls stay identical (stable ordering, no shared state).
  assert.deepEqual(
    summarizeTraceExport(sessions).sessions.map((row) => row.id),
    summary.sessions.map((row) => row.id),
  );
});

test("falls back to stable names when the API has no agent label", () => {
  const summary = summarizeTraceExport([
    session({ id: "root-plain", span_count: 2 }),
    session({
      id: "child-plain",
      parent_session_id: "root-plain",
      ordinal: 1,
      children: [],
    }),
  ]);
  assert.equal(summary.main[0].name, "Main agent");
  assert.equal(summary.subagents[0].name, "child-plain");
});

test("duplicate session ids are rendered once", () => {
  const duplicate = session({ id: "root", agent_name: "Main agent" });
  const summary = summarizeTraceExport([duplicate, duplicate]);
  assert.equal(summary.sessionCount, 1);
  assert.equal(summary.mainAgentCount, 1);
});

test("formats byte counts for the stats grid and confirm button", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(-1), "0 B");
  assert.equal(formatBytes(Number.NaN), "0 B");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(1024), "1 KB");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(612 * 1024), "612 KB");
  assert.equal(formatBytes(Math.round(1.8 * 1024 * 1024)), "1.8 MB");
  assert.equal(formatBytes(1024 ** 3), "1 GB");
});

test("empty input produces zeroed stats without throwing", () => {
  const summary = summarizeTraceExport([], []);
  assert.deepEqual(
    {
      main: summary.mainAgentCount,
      subagents: summary.subagentCount,
      unlinked: summary.unlinkedCount,
      sessions: summary.sessionCount,
      files: summary.rawFileCount,
      totalSize: summary.totalSize,
      estimate: summary.estimatedArchiveSize,
    },
    {
      main: 0,
      subagents: 0,
      unlinked: 0,
      sessions: 0,
      files: 0,
      totalSize: 0,
      estimate: 0,
    },
  );
  assert.deepEqual(buildArchiveTree({
    caseKey: null,
    attemptNo: null,
    subagents: [],
    unlinkedCount: 0,
  }), ["trace-case-attempt-1/", "├── manifest.json", "└── main/"]);
});

test("archive tree follows the backend folder skeleton", () => {
  const summary = summarizeTraceExport(sessions);
  const lines = buildArchiveTree({
    caseKey: "django__django-11019",
    attemptNo: 2,
    subagents: summary.subagents,
    unlinkedCount: summary.unlinkedCount,
  });
  assert.deepEqual(lines, [
    "trace-django__django-11019-attempt-2/",
    "├── manifest.json",
    "├── main/",
    "├── subagents/",
    "│   ├── agent_a1/",
    "│   ├── test_runner/",
    "│   └── agent_47c9/",
    "└── unlinked/",
  ]);
  assert.equal(
    archiveRootName("django__django-11019", 2),
    "trace-django__django-11019-attempt-2",
  );
  assert.equal(archiveRootName("../unsafe key", 0), "trace-unsafekey-attempt-1");
  assert.equal(archiveRootName("", null), "trace-case-attempt-1");
  assert.equal(archiveRootName("评测 case", 4), "trace-case-attempt-4");
  assert.equal(
    archiveRootName("评测", 4, "case-run-1"),
    "trace-case-case-run-1-attempt-4",
  );
});

test("subagent folder names are deduplicated and sanitized", () => {
  const lines = buildArchiveTree({
    caseKey: "case/one",
    attemptNo: 3,
    subagents: [
      { id: "a", name: "same agent", role: "subagent" },
      { id: "b", name: "same agent", role: "subagent" },
      { id: "c", name: "../evil", role: "subagent" },
    ],
    unlinkedCount: 0,
  });
  assert.deepEqual(lines, [
    "trace-one-attempt-3/",
    "├── manifest.json",
    "├── main/",
    "└── subagents/",
    "    ├── sameagent/",
    "    └── evil/",
  ]);
});

test("maps native files to sessions for counts, sizes and tree filenames", () => {
  const mappedSessions = [
    session({
      id: "root",
      trace_file_id: "file-root",
      agent_name: "Main agent",
      children: [
        session({
          id: "child",
          trace_file_id: "file-child",
          parent_session_id: "root",
          agent_name: "test_runner",
          children: [],
        }),
      ],
    }),
    session({
      id: "orphan",
      trace_file_id: "file-orphan",
      status: "unlinked",
      agent_name: "Orphan agent",
      children: [],
    }),
  ];
  const summary = summarizeTraceExport(mappedSessions, [
    { id: "file-root", size: 1024, logical_path: "native/root.jsonl" },
    {
      id: "file-child",
      size: 2048,
      logical_path: "native/subagents/test_runner/agent.jsonl",
    },
    { id: "file-orphan", size: 512, logical_path: "native/orphan.jsonl" },
  ]);

  assert.equal(summary.main[0].fileName, "root.jsonl");
  assert.equal(summary.main[0].fileSize, 1024);
  assert.equal(summary.main[0].fileCount, 1);
  assert.equal(summary.subagents[0].fileName, "agent.jsonl");
  assert.equal(summary.subagents[0].fileSize, 2048);
  assert.equal(summary.unlinked[0].fileName, "orphan.jsonl");

  assert.deepEqual(
    buildArchiveTree({
      caseKey: "case",
      attemptNo: 1,
      main: summary.main,
      subagents: summary.subagents,
      unlinked: summary.unlinked,
      unlinkedCount: 1,
    }),
    [
      "trace-case-attempt-1/",
      "├── manifest.json",
      "├── main/",
      "│   └── root.jsonl",
      "├── subagents/",
      "│   └── test_runner/",
      "│       └── agent.jsonl",
      "└── unlinked/",
      "    └── orphan.jsonl",
    ],
  );
});

test("a native file shared by root and child is projected once, under main", () => {
  const shared = [
    session({
      id: "root",
      trace_file_id: "file-shared",
      agent_name: "Main agent",
      children: [
        session({
          id: "child",
          trace_file_id: "file-shared",
          parent_session_id: "root",
          agent_name: "test_runner",
          children: [],
        }),
      ],
    }),
  ];
  const summary = summarizeTraceExport(shared, [
    { id: "file-shared", size: 10, logical_path: "native/shared.jsonl" },
  ]);
  assert.deepEqual(
    buildArchiveTree({
      caseKey: "case",
      attemptNo: 1,
      main: summary.main,
      subagents: summary.subagents,
      unlinkedCount: 0,
    }),
    [
      "trace-case-attempt-1/",
      "├── manifest.json",
      "└── main/",
      "    └── shared.jsonl",
    ],
  );
});

test("orphan native files are projected under unlinked", () => {
  const summary = summarizeTraceExport([], [
    { id: "file-orphan", size: 5, logical_path: "native/orphan.jsonl" },
  ]);
  assert.deepEqual(summary.orphanFiles, [
    { id: "file-orphan", name: "orphan.jsonl", size: 5 },
  ]);
  assert.deepEqual(
    buildArchiveTree({
      caseKey: "case",
      attemptNo: 1,
      main: [],
      subagents: [],
      orphanFiles: summary.orphanFiles,
      unlinkedCount: 0,
    }),
    [
      "trace-case-attempt-1/",
      "├── manifest.json",
      "├── main/",
      "└── unlinked/",
      "    └── orphan.jsonl",
    ],
  );
});




test("parses ASCII and RFC 5987 archive filenames safely", () => {
  assert.equal(
    parseArchiveFilename('attachment; filename="trace-case-attempt-1.zip"'),
    "trace-case-attempt-1.zip",
  );
  assert.equal(
    parseArchiveFilename(
      "attachment; filename=\"fallback.zip\"; filename*=UTF-8''trace-%E8%BD%A8%E8%BF%B9.zip",
    ),
    "trace-轨迹.zip",
  );
  assert.equal(
    parseArchiveFilename('attachment; filename="../../etc/passwd"'),
    "passwd",
  );
  assert.equal(parseArchiveFilename(null), null);
});

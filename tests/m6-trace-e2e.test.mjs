import assert from "node:assert/strict";
import test from "node:test";

import {
  assertTraceE2EEvidence,
  traceEvidenceChecksum,
} from "../runner/lib/trace-e2e.mjs";

function evidence(overrides = {}) {
  return {
    schema_version: "1.0",
    runtime_key: "claude-code",
    runtime_version: "2.1.278",
    parser_version: "claude-code-2.1.278-v1",
    capture_status: "complete",
    root_session_id: "root-native",
    sessions: [
      {
        id: "session-root",
        native_session_id: "root-native",
        parent_session_id: null,
        status: "ready",
        agent_id: null,
      },
      {
        id: "session-alpha",
        native_session_id: "root-native",
        parent_session_id: "session-root",
        status: "ready",
        agent_id: "agent-alpha",
        relation_evidence: { type: "claude.agent_tool", tool_use_id: "toolu_m6_e2e_alpha" },
      },
      {
        id: "session-beta",
        native_session_id: "root-native",
        parent_session_id: "session-root",
        status: "ready",
        agent_id: "agent-beta",
        relation_evidence: { type: "claude.agent_tool", tool_use_id: "toolu_m6_e2e_beta" },
      },
    ],
    spans: [{ id: "span-root", session_id: "session-root", kind: "AGENT" }],
    trace_files: [
      {
        id: "file-root",
        logical_path: "native/projects/p/root-native.jsonl",
        status: "ready",
      },
    ],
    raw_download: { status: 200, cache_control: "no-store", audit_action: "trace.file.download_url" },
    attempts: [
      { number: 1, trace_status: "ready", session_count: 3 },
      { number: 2, trace_status: "ready", session_count: 3 },
    ],
    ...overrides,
  };
}

test("accepts one real root with two recovered child agents", () => {
  assert.equal(assertTraceE2EEvidence(evidence()).ok, true);
});

test("rejects a fabricated parent with no native evidence", () => {
  const value = evidence();
  value.sessions[1].parent_session_id = "session-beta";
  value.sessions[1].relation_evidence = {};
  assert.throws(() => assertTraceE2EEvidence(value), /parent evidence/i);
});

test("rejects missing attempt isolation and duplicate derived sessions", () => {
  const value = evidence({
    attempts: [
      { number: 1, trace_status: "ready", session_count: 3 },
      { number: 2, trace_status: "ready", session_count: 3 },
    ],
  });
  value.sessions.push({ ...value.sessions[1], id: "session-root" });
  assert.throws(() => assertTraceE2EEvidence(value), /duplicate session id/i);
});

test("rejects leaked credentials in the final evidence", () => {
  const value = evidence({ diagnostics: "Authorization: Bearer abcdefghijklmnop" });
  assert.throws(() => assertTraceE2EEvidence(value), /sensitive value/i);
});

test("evidence checksum is stable and excludes the checksum field", () => {
  const first = traceEvidenceChecksum(evidence());
  const second = traceEvidenceChecksum({ ...evidence(), checksum: "ignored" });
  assert.match(first, /^[0-9a-f]{64}$/);
  assert.equal(first, second);
});

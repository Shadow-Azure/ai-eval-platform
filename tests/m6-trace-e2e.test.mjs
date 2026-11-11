import assert from "node:assert/strict";
import test from "node:test";

import {
  assertTraceE2EEvidence,
  traceE2EClaudeEnv,
  traceEvidenceChecksum,
} from "../runner/lib/trace-e2e.mjs";
import { buildClaudeCommand } from "../runner/lib/runtime-claude-code.mjs";

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
    thinking_span_count: 1,
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
    degraded_path: {
      trace_status: "degraded",
      session_count: 0,
      unknown_format_present: true,
      raw_download_status: 200,
      attempt_status: "succeeded",
      result_status: "resolved",
    },
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

test("trace E2E Claude child receives only the minimal environment allowlist", () => {
  const source = {
    PATH: "/usr/bin",
    TMPDIR: "/tmp/m6",
    LANG: "C.UTF-8",
    S3_SECRET_KEY: "must-not-leak",
    DATABASE_URL: "postgresql://must-not-leak",
    AUTH_SESSION_SECRET: "must-not-leak",
    RUNNER_TASK_SECRET: "must-not-leak",
  };
  const baseEnv = traceE2EClaudeEnv(source);
  const spec = buildClaudeCommand({
    prompt: "test",
    model: "claude-sonnet-4-20250514",
    workspace: "/tmp/workspace",
    runtimeHome: "/tmp/runtime-home",
    gatewayBaseUrl: "http://127.0.0.1:9999",
    gatewayToken: "gateway-only",
    baseEnv,
  });
  assert.equal(spec.env.PATH, "/usr/bin");
  assert.equal(spec.env.TMPDIR, "/tmp/m6");
  for (const key of [
    "S3_SECRET_KEY",
    "DATABASE_URL",
    "AUTH_SESSION_SECRET",
    "RUNNER_TASK_SECRET",
  ]) {
    assert.equal(spec.env[key], undefined);
  }
});

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => key !== "checksum")
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonical(item)]),
    );
  }
  return value;
}

export function traceEvidenceChecksum(value) {
  return createHash("sha256")
    .update(JSON.stringify(canonical(value)))
    .digest("hex");
}

function collectStrings(value, output = []) {
  if (typeof value === "string") output.push(value);
  else if (Array.isArray(value)) value.forEach((item) => collectStrings(item, output));
  else if (value && typeof value === "object") {
    Object.values(value).forEach((item) => collectStrings(item, output));
  }
  return output;
}

/**
 * Validate the durable assertions produced by the real M6 E2E workflow.
 * This function deliberately checks evidence, not the expected implementation:
 * it cannot turn a missing parent, duplicate derived session, or raw download
 * failure into a pass.
 */
export function assertTraceE2EEvidence(value) {
  if (!value || typeof value !== "object") throw new Error("E2E evidence missing");
  if (value.runtime_key !== "claude-code") throw new Error("E2E must use claude-code");
  if (value.runtime_version !== "2.1.278") throw new Error("unexpected Claude runtime version");
  if (value.parser_version !== "claude-code-2.1.278-v1") {
    throw new Error("unexpected parser version");
  }
  if (value.capture_status !== "complete") throw new Error("native capture is incomplete");

  const sessions = value.sessions;
  if (!Array.isArray(sessions)) throw new Error("session evidence is missing");
  const ids = new Set();
  for (const session of sessions) {
    if (!session?.id || ids.has(session.id)) {
      throw new Error(`duplicate session id: ${session?.id ?? "missing"}`);
    }
    ids.add(session.id);
  }
  if (sessions.length !== 3) {
    throw new Error("expected one root and two child sessions");
  }
  const roots = sessions.filter((session) => !session.parent_session_id);
  const children = sessions.filter((session) => session.parent_session_id);
  if (roots.length !== 1 || children.length !== 2) {
    throw new Error("expected exactly one root and two children");
  }
  if (roots[0].native_session_id !== value.root_session_id) {
    throw new Error("root native session mismatch");
  }
  for (const child of children) {
    if (!child.agent_id || !child.relation_evidence?.tool_use_id) {
      throw new Error("parent evidence missing for child session");
    }
    if (child.parent_session_id !== roots[0].id) {
      throw new Error("child parent mismatch");
    }
    if (child.native_session_id !== roots[0].native_session_id) {
      throw new Error("Claude child sessionId must match the native root");
    }
  }

  if (!Array.isArray(value.trace_files) || value.trace_files.length < 1) {
    throw new Error("trace files are missing");
  }
  if (!value.trace_files.every((file) => file.status === "ready")) {
    throw new Error("trace file did not become ready");
  }
  if (!Array.isArray(value.spans) || value.spans.length === 0) {
    throw new Error("span evidence is missing");
  }
  if (value.raw_download?.status !== 200) throw new Error("raw download failed");
  if (value.raw_download?.cache_control !== "no-store") {
    throw new Error("raw download is cacheable");
  }
  if (value.raw_download?.audit_action !== "trace.file.download_url") {
    throw new Error("raw download audit missing");
  }

  if (!Array.isArray(value.attempts) || value.attempts.length < 2) {
    throw new Error("two isolated attempts are required");
  }
  const attemptNumbers = new Set(value.attempts.map((attempt) => attempt.number));
  if (attemptNumbers.size !== value.attempts.length || !attemptNumbers.has(1) || !attemptNumbers.has(2)) {
    throw new Error("attempt isolation failed");
  }
  if (!value.attempts.every((attempt) => attempt.session_count === 3)) {
    throw new Error("attempt session counts are not isolated");
  }

  const text = collectStrings(value).join("\n");
  if (
    /authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}/i.test(text) ||
    /\bsk-[A-Za-z0-9_-]{8,}\b/.test(text) ||
    /\bAKIA[A-Z0-9]{16}\b/.test(text)
  ) {
    throw new Error("sensitive value leaked into E2E evidence");
  }
  return { ok: true, checksum: traceEvidenceChecksum(value) };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const path = process.argv[2];
  if (!path) {
    console.error("usage: node runner/lib/trace-e2e.mjs <evidence.json>");
    process.exit(2);
  }
  try {
    const value = JSON.parse(readFileSync(path, "utf8"));
    console.log(JSON.stringify(assertTraceE2EEvidence(value)));
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}

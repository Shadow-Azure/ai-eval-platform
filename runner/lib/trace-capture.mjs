/**
 * Native trace capture for the in-job runner (M6-01).
 *
 * The authoritative Claude Code files live under the isolated runtime HOME:
 * `projects/<project>/<session>.jsonl`, `projects/<project>/subagents/*.jsonl`
 * and the matching `*.meta.json` relation metadata. This module copies those
 * files next to the independent stdout/stderr diagnostics, computes digests and
 * emits a manifest whose paths are logical: host absolute paths never enter the
 * manifest or the uploaded objects.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { basename, join, relative, sep } from "node:path";

export const TRACE_MANIFEST_VERSION = "1.0";
export const MAX_LOGICAL_PATH = 512;
export const TRACE_ARTIFACT_KINDS = [
  "trace",
  "trace_metadata",
  "trace_diagnostic",
];

const SAFE_LOGICAL_PATH = /^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/;
const SAFE_COMPONENT = /^[A-Za-z0-9._-]+$/;

export class TraceCaptureError extends Error {
  constructor(message, { code = "trace_capture_invalid" } = {}) {
    super(message);
    this.name = "TraceCaptureError";
    this.code = code;
  }
}

/** Whether a value is a safe, platform-neutral relative path. */
export function isSafeLogicalPath(value) {
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_LOGICAL_PATH) {
    return false;
  }
  if (value.includes("\0") || value.includes("\\") || value.startsWith("/")) return false;
  if (!SAFE_LOGICAL_PATH.test(value)) return false;
  return value.split("/").every((part) => part !== "" && part !== "." && part !== "..");
}

function sanitizeComponent(part) {
  const sanitized = SAFE_COMPONENT.test(part) ? part : part.replace(/[^A-Za-z0-9._-]/g, "_");
  if (!sanitized || sanitized === "." || sanitized === "..") return "_";
  return sanitized;
}

function sanitizeRelative(relativePath) {
  const parts = relativePath.split(sep).filter(Boolean).map(sanitizeComponent);
  const logicalPath = parts.join("/");
  if (!isSafeLogicalPath(logicalPath)) {
    throw new TraceCaptureError(`无法生成安全逻辑路径：${relativePath}`, {
      code: "trace_path_invalid",
    });
  }
  return logicalPath;
}

function contentTypeFor(logicalPath) {
  if (logicalPath.endsWith(".jsonl") || logicalPath.endsWith(".ndjson")) {
    return "application/x-ndjson";
  }
  if (logicalPath.endsWith(".json")) return "application/json";
  return "text/plain";
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function walkFiles(root, visit) {
  if (!existsSync(root)) return;
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const full = join(root, entry.name);
    if (entry.isDirectory()) walkFiles(full, visit);
    else if (entry.isFile()) visit(full);
  }
}

function fileEntry({ sourcePath, logicalPath, artifactKind, nativeFormat, formatVersion, runtimeVersion }) {
  const bytes = readFileSync(sourcePath);
  return {
    logical_path: logicalPath,
    artifact_kind: artifactKind,
    content_type: contentTypeFor(logicalPath),
    native_format: nativeFormat,
    format_version: formatVersion,
    runtime_version: runtimeVersion,
    sha256: sha256(bytes),
    size: bytes.length,
    bytes,
  };
}

/** The structured stream's `system` line names the root native session. */
export function readRootSessionId(stdoutPath) {
  if (!existsSync(stdoutPath)) return null;
  for (const line of readFileSync(stdoutPath, "utf8").split("\n")) {
    const text = line.trim();
    if (!text) continue;
    try {
      const item = JSON.parse(text);
      if (item && typeof item === "object") {
        const sessionId = item.session_id ?? item.sessionId;
        if (typeof sessionId === "string" && sessionId) return sessionId;
      }
    } catch {
      // A non-JSON diagnostic line is not the session header.
    }
  }
  return null;
}

function classifyNative(logicalPath) {
  const isMeta = logicalPath.endsWith(".meta.json");
  const isSubagent = logicalPath.includes("/subagents/");
  if (isMeta) {
    return {
      artifact_kind: "trace_metadata",
      native_format: isSubagent
        ? "claude-code.subagent.meta+json"
        : "claude-code.meta+json",
    };
  }
  return {
    artifact_kind: "trace",
    native_format: isSubagent
      ? "claude-code.subagent.session+jsonl"
      : "claude-code.session+jsonl",
  };
}

function captureReasons(files, rootSessionId) {
  const reasons = new Set();
  const sessions = files.filter((file) => file.artifact_kind === "trace");
  const rootSessions = sessions.filter((file) => !file.logical_path.includes("/subagents/"));
  const subagentSessions = sessions.filter((file) => file.logical_path.includes("/subagents/"));
  const metadata = files.filter((file) => file.artifact_kind === "trace_metadata");

  if (rootSessions.length === 0) reasons.add("native_session_missing");
  if (rootSessionId) {
    const expected = `${rootSessionId}.jsonl`;
    const present = rootSessions.some((file) => basename(file.logical_path) === expected);
    if (!present) reasons.add("root_session_missing");
  }

  const sessionNames = new Set(subagentSessions.map((file) => basename(file.logical_path)));
  const metaNames = new Set(metadata.map((file) => basename(file.logical_path)));
  for (const name of sessionNames) {
    const meta = name.replace(/\.jsonl$/, ".meta.json");
    if (!metaNames.has(meta)) reasons.add("subagent_metadata_missing");
  }
  for (const name of metaNames) {
    const session = name.replace(/\.meta\.json$/, ".jsonl");
    if (!sessionNames.has(session)) reasons.add("subagent_session_missing");
  }
  if (!files.some((file) => file.logical_path === "diagnostics/stdout.jsonl")) {
    reasons.add("stdout_missing");
  }
  return [...reasons].sort();
}

/**
 * Collect every native session/meta file plus stdout/stderr diagnostics.
 *
 * Returns `files` (with bytes) and the serializable capture metadata that the
 * completion callback turns into a multi-file `trace_manifest`.
 */
export function collectTraceBundle({
  runtimeHome,
  traceDir,
  runtimeKey = "claude-code",
  runtimeVersion = null,
  adapterVersion = "claude-code-1.0",
} = {}) {
  if (!traceDir) throw new TraceCaptureError("缺少 traceDir");
  const files = [];
  const skipped = new Set();
  const add = (entry) => {
    if (skipped.has(entry.logical_path)) return;
    skipped.add(entry.logical_path);
    files.push(entry);
  };

  const stdoutPath = join(traceDir, "claude-stream.jsonl");
  const stderrPath = join(traceDir, "claude-stderr.log");
  if (existsSync(stdoutPath)) {
    add(
      fileEntry({
        sourcePath: stdoutPath,
        logicalPath: "diagnostics/stdout.jsonl",
        artifactKind: "trace_diagnostic",
        nativeFormat: "claude-code.stdout.stream-json",
        formatVersion: "1",
        runtimeVersion,
      }),
    );
  }
  if (existsSync(stderrPath)) {
    add(
      fileEntry({
        sourcePath: stderrPath,
        logicalPath: "diagnostics/stderr.log",
        artifactKind: "trace_diagnostic",
        nativeFormat: "claude-code.stderr",
        formatVersion: "1",
        runtimeVersion,
      }),
    );
  }

  const diagnosticsDir = join(traceDir, "diagnostics");
  walkFiles(diagnosticsDir, (sourcePath) => {
    const logicalPath = sanitizeRelative(relative(traceDir, sourcePath));
    add(
      fileEntry({
        sourcePath,
        logicalPath,
        artifactKind: "trace_diagnostic",
        nativeFormat: "runner.diagnostic",
        formatVersion: "1",
        runtimeVersion,
      }),
    );
  });

  const nativeRoot = runtimeHome ? join(runtimeHome, ".claude") : null;
  const projectsRoot = nativeRoot ? join(nativeRoot, "projects") : null;
  walkFiles(projectsRoot, (sourcePath) => {
    if (!sourcePath.endsWith(".jsonl") && !sourcePath.endsWith(".meta.json")) return;
    const rawRelative = relative(nativeRoot, sourcePath).split(sep).join("/");
    const logicalPath = `native/${sanitizeRelative(rawRelative)}`;
    const { artifact_kind: artifactKind, native_format: nativeFormat } = classifyNative(logicalPath);
    add(
      fileEntry({
        sourcePath,
        logicalPath,
        artifactKind,
        nativeFormat,
        formatVersion: "1",
        runtimeVersion,
      }),
    );
  });

  files.sort((left, right) => left.logical_path.localeCompare(right.logical_path));
  const incompleteReasons = captureReasons(files, readRootSessionId(stdoutPath));
  return {
    schema_version: TRACE_MANIFEST_VERSION,
    runtime_key: runtimeKey,
    runtime_version: runtimeVersion,
    adapter_version: adapterVersion,
    capture_status: incompleteReasons.length === 0 ? "complete" : "partial",
    incomplete_reasons: incompleteReasons,
    files,
  };
}

/** Write a diagnostic fallback so a failed run still uploads one object. */
export function writeDiagnostic(traceDir, name, text) {
  const directory = join(traceDir, "diagnostics");
  mkdirSync(directory, { recursive: true });
  const destination = join(directory, name);
  writeFileSync(destination, text);
  return destination;
}

/**
 * Collapse repeated digests and reject ambiguous logical paths before upload.
 * Identical bytes are one object; one logical path can never point at two
 * different digests.
 */
export function dedupeTraceObjects(entries) {
  const byDigest = new Map();
  const pathDigest = new Map();
  for (const entry of entries) {
    if (!isSafeLogicalPath(entry.logical_path)) {
      throw new TraceCaptureError(`非法逻辑路径：${entry.logical_path}`, {
        code: "trace_path_invalid",
      });
    }
    const bytes = Buffer.isBuffer(entry.bytes) ? entry.bytes : Buffer.from(entry.bytes ?? "");
    const digest = entry.sha256 ?? sha256(bytes);
    const known = pathDigest.get(entry.logical_path);
    if (known && known !== digest) {
      throw new TraceCaptureError(`同一逻辑路径对应多个 digest：${entry.logical_path}`, {
        code: "trace_manifest_conflict",
      });
    }
    pathDigest.set(entry.logical_path, digest);
    const key = `${entry.artifact_kind}\0${digest}`;
    if (byDigest.has(key)) continue;
    byDigest.set(key, { ...entry, sha256: digest, size: bytes.length, bytes });
  }
  return [...byDigest.values()].sort((left, right) =>
    left.logical_path.localeCompare(right.logical_path),
  );
}

/** Serializable entries for upload/completion (no bytes, no host paths). */
export function traceManifestFromBundle(bundle, reportedObjects) {
  const traceKinds = new Set(TRACE_ARTIFACT_KINDS);
  const clean = (entry) => {
    const result = {};
    for (const [key, value] of Object.entries(entry)) {
      if (value !== undefined && value !== null && value !== "") result[key] = value;
    }
    return result;
  };
  const objects = reportedObjects
    .filter((object) => traceKinds.has(object.artifact_kind))
    .map((object) =>
      clean({
        artifact_kind: object.artifact_kind,
        logical_path: object.logical_path,
        object_key: object.object_key,
        sha256: object.sha256,
        size: object.size,
        content_type: object.content_type,
        native_format: object.native_format,
        runtime_version: object.runtime_version ?? bundle.runtime_version ?? "unknown",
        format_version: object.format_version ?? "1",
      }),
    );
  return clean({
    manifest_version: TRACE_MANIFEST_VERSION,
    capture_status: bundle.capture_status,
    incomplete_reasons: bundle.incomplete_reasons,
    runtime_version: bundle.runtime_version,
    adapter_version: bundle.adapter_version,
    format_version: "1",
    objects,
  });
}

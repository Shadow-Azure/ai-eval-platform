import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

/**
 * Manifest transport shared by the shard scripts (real-task / plan-cases).
 *
 * M2-05: HTTP(S) responses carry the platform-issued short-term token in
 * X-Runner-Task-Token plus the optional tasks/model endpoints; file:// is the
 * local mode and requires a token from the environment instead. Callers still
 * own digest verification and schema validation so a single place cannot
 * silently weaken them.
 *
 * M8-51 (#323) option C: a minimal 1.1 dispatch no longer carries the manifest
 * digest or the dispatch expiry. The manifest response is the server
 * descriptor for both (X-Manifest-Digest / X-Manifest-Expires-At), so this
 * helper forwards them and callers fall back to them when the dispatch lacks
 * the legacy fields.
 */
export async function fetchManifest(manifestUrl) {
  if (manifestUrl.startsWith("file://")) {
    return { bytes: readFileSync(new URL(manifestUrl)), tokenHeader: null };
  }
  const response = await fetch(manifestUrl, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`manifest 拉取失败：HTTP ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  return {
    bytes,
    tokenHeader: response.headers.get("x-runner-task-token"),
    manifestDigest: response.headers.get("x-manifest-digest"),
    expiresAt: response.headers.get("x-manifest-expires-at"),
    tasksUrl: response.headers.get("x-runner-tasks-url"),
    modelToken: response.headers.get("x-runner-model-token"),
  };
}

export function sha256(text) {
  return `sha256:${createHash("sha256").update(text).digest("hex")}`;
}

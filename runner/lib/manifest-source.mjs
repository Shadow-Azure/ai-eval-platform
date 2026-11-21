import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";

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
 *
 * M8 (#358): passing `caseRunId` asks the platform for a model token scoped to
 * that case attempt. The query parameter does not change the manifest body or
 * its digest; it only changes the minted token.
 */

const MANIFEST_TIMEOUT_MS = 10_000;

/**
 * M8 (#362): the per-case refresh runs after two synchronous blockers
 * (`docker load` through spawnSync and the base-commit checkout), so a pooled
 * keep-alive socket may have been closed by the platform while the event loop
 * was blocked. `fetch` then reuses that dead socket and fails immediately with
 * `UND_ERR_SOCKET` instead of opening a new one. `agent: false` makes every
 * call open a fresh TCP connection; `connection: close` also keeps the server
 * from holding the request socket open after the response.
 */
function fetchOnce(url, { timeoutMs = MANIFEST_TIMEOUT_MS } = {}) {
  const request = url.protocol === "https:" ? httpsRequest : httpRequest;
  return new Promise((resolve, reject) => {
    const req = request(
      url,
      {
        method: "GET",
        agent: false,
        timeout: timeoutMs,
        headers: { connection: "close" },
      },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("error", reject);
        response.on("end", () => {
          const status = response.statusCode ?? 0;
          if (status < 200 || status >= 300) {
            reject(new Error(`manifest 拉取失败：HTTP ${status}`));
            return;
          }
          resolve({ headers: response.headers, bytes: Buffer.concat(chunks) });
        });
      },
    );
    req.on("timeout", () => req.destroy(new Error(`manifest 拉取超时（${timeoutMs}ms）`)));
    req.on("error", reject);
    req.end();
  });
}

function responseHeader(headers, name) {
  const value = headers[name];
  return Array.isArray(value) ? value[0] : value ?? null;
}

export async function fetchManifest(manifestUrl, { caseRunId = null } = {}) {
  if (manifestUrl.startsWith("file://")) {
    return { bytes: readFileSync(new URL(manifestUrl)), tokenHeader: null };
  }
  const requestUrl = new URL(manifestUrl);
  if (caseRunId) requestUrl.searchParams.set("case_run_id", caseRunId);
  const response = await fetchOnce(requestUrl);
  return {
    bytes: response.bytes,
    tokenHeader: responseHeader(response.headers, "x-runner-task-token"),
    manifestDigest: responseHeader(response.headers, "x-manifest-digest"),
    expiresAt: responseHeader(response.headers, "x-manifest-expires-at"),
    tasksUrl: responseHeader(response.headers, "x-runner-tasks-url"),
    modelToken: responseHeader(response.headers, "x-runner-model-token"),
  };
}

/**
 * Transport diagnostics for a failed manifest fetch. `fetch` used to carry
 * the real socket failure in `error.cause` (for example UND_ERR_SOCKET); the
 * one-shot http client reports the same class of failure as `error.code`
 * (for example ECONNREFUSED). Both are kept so the runner log names the
 * transport cause instead of only the generic "fetch failed".
 */
export function manifestErrorDetail(error) {
  const cause = error?.cause && typeof error.cause === "object" ? error.cause : null;
  const code = cause?.code ?? error?.code ?? null;
  const message = cause?.message ?? null;
  return {
    causeCode: code == null ? null : String(code),
    causeMessage: message == null ? null : String(message).slice(0, 200),
  };
}

export function sha256(text) {
  return `sha256:${createHash("sha256").update(text).digest("hex")}`;
}

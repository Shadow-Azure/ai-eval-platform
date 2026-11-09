import { createServer } from "node:http";

/**
 * Minimal platform stub used by the contract tests. It never calls a model.
 *
 * M2-05 additions:
 * - GET /manifest also returns X-Runner-Task-Token when `expectedToken` is
 *   set (platform-issued short-term token delivery, like the real endpoint).
 * - behavior.uploadUrls serves POST .../upload-urls with presigned-style PUT
 *   URLs (`/objects/<key>`, no bearer auth — the URL signature is the
 *   credential) and records every PUT body for assertions.
 *
 * M4-01 addition:
 * - behavior.startedCapacityFailures answers POST .../started with 429
 *   capacity_waiting that many times, so the runner's per-evaluation cap wait
 *   is exercised without failing the attempt.
 */
export async function startMockPlatform({
  manifestText,
  expectedToken,
  behavior = {},
  manifestContentType = "application/json",
}) {
  const getManifest = typeof manifestText === "function" ? manifestText : () => manifestText;
  const requests = [];
  const objects = new Map();
  const state = {
    completionFailures: behavior.completionFailures ?? 0,
    // M4-01: answers `started` with 429 capacity_waiting this many times, so
    // tests can prove the runner waits for the per-evaluation cap instead of
    // failing the attempt.
    startedCapacityFailures: behavior.startedCapacityFailures ?? 0,
    failCompletionPermanently: behavior.failCompletionPermanently ?? false,
    failFailureCallback: behavior.failFailureCallback ?? false,
    staleAttempt: behavior.staleAttempt ?? false,
    expectedAttempt: behavior.expectedAttempt ?? 1,
  };

  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const rawBody = Buffer.concat(chunks).toString("utf8");
    let body = null;
    try {
      body = rawBody ? JSON.parse(rawBody) : null;
    } catch {
      body = rawBody;
    }
    requests.push({ method: request.method, path: request.url, headers: request.headers, body });

    const send = (status, payload) => {
      response.writeHead(status, { "content-type": "application/json" });
      response.end(JSON.stringify(payload));
    };

    if (request.method === "GET" && request.url === "/manifest") {
      const headers = { "content-type": manifestContentType };
      if (expectedToken) headers["x-runner-task-token"] = expectedToken;
      response.writeHead(200, headers);
      response.end(getManifest());
      return;
    }

    // Presigned PUT receiver: the URL signature is the credential, so no
    // bearer auth here — mirrors the real object store.
    if (request.method === "PUT" && request.url.startsWith("/objects/")) {
      const key = request.url.slice("/objects/".length);
      objects.set(key, Buffer.from(rawBody, "utf8"));
      response.writeHead(200, { "content-type": "application/json" });
      response.end("{}");
      return;
    }

    if (expectedToken && request.headers.authorization !== `Bearer ${expectedToken}`) {
      send(401, { error: { code: "token_invalid" } });
      return;
    }

    if (request.method !== "POST") {
      send(404, { error: { code: "not_found" } });
      return;
    }

    const path = request.url ?? "";
    if (path.endsWith("/upload-urls")) {
      if (!behavior.uploadUrls) {
        send(404, { error: { code: "not_found" } });
        return;
      }
      const uploads = (body?.objects ?? []).map((item) => {
        const objectKey = `mock/${item.artifact_kind}/${item.sha256}`;
        return {
          artifact_kind: item.artifact_kind,
          sha256: item.sha256,
          size: item.size,
          object_key: objectKey,
          upload_url: `${state.baseUrl}/objects/${objectKey}`,
          expires_at: new Date(Date.now() + 900_000).toISOString(),
          method: "PUT",
          headers: { "content-type": item.content_type },
        };
      });
      send(200, { ok: true, uploads });
      return;
    }
    if (path.endsWith("/started")) {
      if (state.startedCapacityFailures > 0) {
        state.startedCapacityFailures -= 1;
        send(429, { error: { code: "capacity_waiting" } });
        return;
      }
      send(200, { ok: true });
      return;
    }
    if (path.endsWith("/uploads") || path.endsWith("/heartbeat")) {
      send(200, { ok: true });
      return;
    }
    if (path.endsWith("/failed")) {
      send(state.failFailureCallback ? 500 : 200, { ok: !state.failFailureCallback });
      return;
    }
    if (path.endsWith("/completed")) {
      if (state.staleAttempt && body?.attempt !== state.expectedAttempt) {
        send(409, { error: { code: "stale_attempt" } });
        return;
      }
      if (state.failCompletionPermanently) {
        send(400, { error: { code: "callback_invalid" } });
        return;
      }
      if (state.completionFailures > 0) {
        state.completionFailures -= 1;
        send(500, { error: { code: "temporary_failure" } });
        return;
      }
      send(200, { ok: true });
      return;
    }

    send(404, { error: { code: "not_found" } });
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const baseUrl = `http://127.0.0.1:${address.port}`;
  state.baseUrl = baseUrl;

  return {
    baseUrl,
    requests,
    state,
    objects,
    close: () => new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
    callbackUrl: (caseRunId, action) => `${baseUrl}/runner/v1/case-runs/${caseRunId}/${action}`,
  };
}

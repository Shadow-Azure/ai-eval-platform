import { createServer } from "node:http";

/** Minimal platform stub used by the contract tests. It never calls a model. */
export async function startMockPlatform({
  manifestText,
  expectedToken,
  behavior = {},
  manifestContentType = "application/json",
}) {
  const getManifest = typeof manifestText === "function" ? manifestText : () => manifestText;
  const requests = [];
  const state = {
    completionFailures: behavior.completionFailures ?? 0,
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
      response.writeHead(200, { "content-type": manifestContentType });
      response.end(getManifest());
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
    if (path.endsWith("/uploads") || path.endsWith("/started") || path.endsWith("/heartbeat")) {
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

  return {
    baseUrl,
    requests,
    state,
    close: () => new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
    callbackUrl: (caseRunId, action) => `${baseUrl}/runner/v1/case-runs/${caseRunId}/${action}`,
  };
}

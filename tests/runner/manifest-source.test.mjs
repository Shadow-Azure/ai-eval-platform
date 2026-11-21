import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createServer } from "node:http";
import test from "node:test";

import { fetchManifest, manifestErrorDetail } from "../../runner/lib/manifest-source.mjs";

/**
 * M8 (#362): the runner blocks its event loop for tens of seconds between the
 * early manifest fetch and the per-case refresh (image tarball load and base
 * checkout). A keep-alive socket the platform closed in that window used to
 * make the refresh fail with UND_ERR_SOCKET, which fail-closed turned into
 * `model_token_unavailable` for every case.
 */

const MANIFEST_BODY = `${JSON.stringify({ schema_version: "1.0", cases: [] })}\n`;

function startManifestServer({ keepAliveTimeout = 5_000 } = {}) {
  const requests = [];
  const server = createServer((request, response) => {
    requests.push({
      url: request.url,
      connection: request.headers.connection,
      remotePort: request.socket.remotePort,
    });
    const caseScoped = request.url.includes("case_run_id=");
    response.writeHead(200, {
      "content-type": "application/json",
      "x-runner-task-token": "task-token",
      "x-runner-model-token": caseScoped ? "case-token" : "shard-token",
      "x-manifest-digest": "sha256:manifest",
    });
    response.end(MANIFEST_BODY);
  });
  server.keepAliveTimeout = keepAliveTimeout;
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const url = `http://127.0.0.1:${server.address().port}/manifest`;
      resolve({
        requests,
        url,
        closeIdleConnections: () => server.closeIdleConnections(),
        close: () =>
          new Promise((done) => {
            server.closeAllConnections?.();
            server.close(done);
          }),
      });
    });
  });
}

test("every manifest fetch opens a non-pooled connection", async () => {
  const platform = await startManifestServer();
  try {
    const first = await fetchManifest(platform.url);
    const second = await fetchManifest(platform.url, { caseRunId: "ecr_case" });

    assert.equal(first.bytes.toString(), MANIFEST_BODY);
    assert.equal(first.tokenHeader, "task-token");
    assert.equal(first.manifestDigest, "sha256:manifest");
    assert.equal(first.modelToken, "shard-token");
    assert.equal(second.modelToken, "case-token");
    assert.equal(platform.requests[1].url, "/manifest?case_run_id=ecr_case");

    // The fix's actual contract: each refresh asks for a fresh TCP connection
    // instead of reusing a socket that may already be closed server-side.
    assert.equal(platform.requests[0].connection, "close");
    assert.equal(platform.requests[1].connection, "close");
    assert.notEqual(
      platform.requests[0].remotePort,
      platform.requests[1].remotePort,
      "the per-case refresh must not reuse the earlier connection",
    );
  } finally {
    await platform.close();
  }
});

test("the refresh survives an idle server close while the client blocks synchronously", async () => {
  const platform = await startManifestServer();
  try {
    await fetchManifest(platform.url);
    // The server closes the idle keep-alive socket (uvicorn's five-second
    // default) and the runner then blocks long enough that it cannot process
    // the FIN before asking for the case token.
    platform.closeIdleConnections();
    spawnSync("sleep", ["0.3"]);

    const refreshed = await fetchManifest(platform.url, { caseRunId: "ecr_case" });
    assert.equal(refreshed.modelToken, "case-token");
    assert.equal(platform.requests.length, 2);
    assert.equal(platform.requests[1].url, "/manifest?case_run_id=ecr_case");
    assert.notEqual(
      platform.requests[0].remotePort,
      platform.requests[1].remotePort,
      "the refresh must dial a fresh connection after the idle close",
    );
  } finally {
    await platform.close();
  }
});

test("a non-2xx manifest response is rejected", async () => {
  const server = createServer((request, response) => {
    response.writeHead(503, { "content-type": "application/json" });
    response.end("{}");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/manifest`;
  try {
    await assert.rejects(fetchManifest(url), /manifest 拉取失败：HTTP 503/);
  } finally {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  }
});

test("manifest error detail keeps the transport cause", () => {
  const fetchFailure = new Error("fetch failed");
  fetchFailure.cause = Object.assign(new Error("other side closed"), {
    code: "UND_ERR_SOCKET",
  });
  assert.deepEqual(manifestErrorDetail(fetchFailure), {
    causeCode: "UND_ERR_SOCKET",
    causeMessage: "other side closed",
  });

  const socketFailure = Object.assign(new Error("connect ECONNREFUSED"), {
    code: "ECONNREFUSED",
  });
  assert.deepEqual(manifestErrorDetail(socketFailure), {
    causeCode: "ECONNREFUSED",
    causeMessage: null,
  });

  assert.deepEqual(manifestErrorDetail(new Error("plain")), {
    causeCode: null,
    causeMessage: null,
  });
});

test("a refused connection reaches the caller with its socket code", async () => {
  const probe = await startManifestServer();
  const url = probe.url;
  await probe.close();
  const error = await fetchManifest(url).catch((failure) => failure);
  assert.ok(error instanceof Error);
  assert.equal(manifestErrorDetail(error).causeCode, "ECONNREFUSED");
});

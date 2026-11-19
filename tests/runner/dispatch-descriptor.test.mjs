import assert from "node:assert/strict";
import test from "node:test";

import {
  dispatchExpiresAt,
  dispatchManifestDigest,
  isExpired,
} from "../../runner/lib/dispatch-descriptor.mjs";

test("descriptor fields prefer the legacy dispatch values when present", () => {
  const dispatch = {
    manifest_digest: `sha256:${"a".repeat(64)}`,
    expires_at: "2026-11-19T00:00:00Z",
  };
  const response = {
    manifestDigest: `sha256:${"b".repeat(64)}`,
    expiresAt: "2026-11-20T00:00:00Z",
  };
  assert.equal(dispatchManifestDigest(dispatch, response), dispatch.manifest_digest);
  assert.equal(dispatchExpiresAt(dispatch, response), dispatch.expires_at);
});

test("a minimal 1.1 dispatch falls back to the manifest response descriptor", () => {
  const response = {
    manifestDigest: `sha256:${"c".repeat(64)}`,
    expiresAt: "2026-11-20T00:00:00Z",
  };
  assert.equal(dispatchManifestDigest({}, response), response.manifestDigest);
  assert.equal(dispatchExpiresAt({}, response), response.expiresAt);
});

test("missing descriptor values stay null so callers fail explicitly", () => {
  assert.equal(dispatchManifestDigest({}, {}), null);
  assert.equal(dispatchExpiresAt({}, {}), null);
  assert.equal(dispatchManifestDigest({ manifest_url: "file:///tmp/m.json" }, {}), null);
});

test("isExpired only rejects a concrete past timestamp", () => {
  assert.equal(isExpired(null), false);
  assert.equal(isExpired(undefined), false);
  assert.equal(isExpired(new Date(Date.now() + 60_000).toISOString()), false);
  assert.equal(isExpired(new Date(Date.now() - 60_000).toISOString()), true);
});

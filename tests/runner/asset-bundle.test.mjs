import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import test from "node:test";

import { materializeSnapshot, parseAssetBundle } from "../../runner/lib/asset-bundle.mjs";

function sha256(text) {
  return `sha256:${createHash("sha256").update(text).digest("hex")}`;
}

function bundleFor(files, { snapshotId = "snap-1", commit = "a".repeat(40) } = {}) {
  const entries = Object.keys(files)
    .sort()
    .map((path) => ({
      path,
      sha256: sha256(files[path]),
      size: Buffer.byteLength(files[path]),
      content_base64: Buffer.from(files[path]).toString("base64"),
    }));
  const treeDigest = sha256(
    entries.map((item) => `${item.path}\0${item.sha256}`).join("\n"),
  );
  const document = {
    schema_version: "1.0",
    snapshot_id: snapshotId,
    resolved_commit: commit,
    tree_digest: treeDigest,
    files: entries,
  };
  const gzip = gzipSync(Buffer.from(JSON.stringify(document)));
  return {
    gzip,
    snapshot: {
      snapshot_id: snapshotId,
      resolved_commit: commit,
      tree_digest: treeDigest,
      asset_count: 2,
      bundle_digest: sha256(gzip),
    },
  };
}

const assetFiles = {
  "skills/brainstorming/SKILL.md": "# brainstorming\n",
  "commands/ship-check.md": "# ship\n",
};

test("materializes exactly the verified snapshot and removes stale assets", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "asset-bundle-"));
  mkdirSync(join(workspace, ".claude", "skills", "stale"), { recursive: true });
  writeFileSync(join(workspace, ".claude", "skills", "stale", "SKILL.md"), "# stale\n");

  const { gzip, snapshot } = bundleFor(assetFiles);
  const bundlePath = join(workspace, "bundle.json.gz");
  writeFileSync(bundlePath, gzip);

  const written = await materializeSnapshot(
    { ...snapshot, bundle_url: `file://${bundlePath}` },
    workspace,
  );
  assert.deepEqual(written, [".claude/commands/ship-check.md", ".claude/skills/brainstorming/SKILL.md"]);
  assert.equal(
    readFileSync(join(workspace, ".claude", "skills", "brainstorming", "SKILL.md"), "utf8"),
    "# brainstorming\n",
  );
  assert.equal(readFileSync(join(workspace, ".claude", "commands", "ship-check.md"), "utf8"), "# ship\n");
  assert.throws(
    () => readFileSync(join(workspace, ".claude", "skills", "stale", "SKILL.md")),
    /ENOENT/,
  );
});

test("rejects a tampered bundle or mismatched tree digest", () => {
  const { gzip, snapshot } = bundleFor(assetFiles);
  const parsed = parseAssetBundle(gzip, snapshot);
  assert.deepEqual(Object.keys(parsed).sort(), Object.keys(assetFiles).sort());

  assert.throws(
    () => parseAssetBundle(Buffer.concat([gzip, Buffer.from("x")]), snapshot),
    /digest/,
  );
  assert.throws(
    () => parseAssetBundle(gzip, { ...snapshot, tree_digest: sha256("wrong") }),
    /资产树 digest/,
  );
  assert.throws(
    () => parseAssetBundle(gzip, { ...snapshot, resolved_commit: "b".repeat(40) }),
    /commit/,
  );
});

test("missing snapshot means no injection; missing URL is an explicit failure", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "asset-bundle-"));
  assert.deepEqual(await materializeSnapshot({}, workspace), []);
  await assert.rejects(
    () =>
      materializeSnapshot(
        { snapshot_id: "snap-1", asset_count: 1, resolved_commit: "a".repeat(40) },
        workspace,
      ),
    (error) => error.code === "asset_bundle_missing",
  );
});

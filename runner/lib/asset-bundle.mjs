/**
 * Download, verify and materialize one immutable Agent asset snapshot (M5-01).
 *
 * The platform packages selected assets as a deterministic gzip JSON bundle in
 * OSS. The runner never reads Git, never sees a GitHub credential, and only
 * writes the files whose digests match the frozen snapshot. Any mismatch is a
 * hard case failure (`asset_invalid`), not a silent fallback to built-ins.
 */
import { createHash } from "node:crypto";
import { chmodSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { join, resolve } from "node:path";

import { materializeAssets, RuntimeError } from "./runtime-claude-code.mjs";

const BUNDLE_SCHEMA = "1.0";

function digest(text) {
  return `sha256:${createHash("sha256").update(text).digest("hex")}`;
}

function safeRelative(path) {
  if (typeof path !== "string" || !path || path.includes("\0") || path.includes("\\")) return null;
  if (path.startsWith("/")) return null;
  const parts = path.split("/");
  if (parts.some((part) => part === "" || part === "." || part === "..")) return null;
  return parts;
}

/** Download the bundle bytes; supports https presigned URLs and file:// tests. */
export async function downloadAssetBundle(assetSnapshot, { fetchImpl = fetch } = {}) {
  const url = assetSnapshot?.bundle_url;
  if (!url) {
    throw new RuntimeError("资产快照缺少 bundle_url", { code: "asset_bundle_missing" });
  }
  if (String(url).startsWith("file://")) {
    return readFileSync(new URL(url));
  }
  const response = await fetchImpl(url, { signal: AbortSignal.timeout(60000) });
  if (!response.ok) {
    throw new RuntimeError(`资产包下载失败：HTTP ${response.status}`, {
      code: "asset_bundle_unavailable",
    });
  }
  return Buffer.from(await response.arrayBuffer());
}

/** Parse and verify a bundle against the frozen snapshot metadata. */
export function parseAssetBundleDetailed(bytes, assetSnapshot) {
  const expected = String(assetSnapshot?.bundle_digest || "");
  if (!expected || digest(bytes) !== expected) {
    throw new RuntimeError("资产包 digest 不匹配", { code: "asset_digest_mismatch" });
  }
  let document;
  try {
    document = JSON.parse(gunzipSync(bytes).toString("utf8"));
  } catch {
    throw new RuntimeError("资产包不是有效的 gzip JSON", { code: "asset_bundle_invalid" });
  }
  if (!document || typeof document !== "object" || document.schema_version !== BUNDLE_SCHEMA) {
    throw new RuntimeError("资产包 schema 不受支持", { code: "asset_bundle_invalid" });
  }
  if (assetSnapshot.snapshot_id && document.snapshot_id !== assetSnapshot.snapshot_id) {
    throw new RuntimeError("资产包 snapshot_id 不匹配", { code: "asset_snapshot_mismatch" });
  }
  if (assetSnapshot.resolved_commit && document.resolved_commit !== assetSnapshot.resolved_commit) {
    throw new RuntimeError("资产包 commit 不匹配", { code: "asset_commit_mismatch" });
  }
  const files = {};
  const digests = {};
  const modes = {};
  for (const item of Array.isArray(document.files) ? document.files : []) {
    if (!item || typeof item !== "object") {
      throw new RuntimeError("资产包条目无效", { code: "asset_bundle_invalid" });
    }
    const parts = safeRelative(item.path);
    if (!parts) {
      throw new RuntimeError("资产包路径越界", { code: "asset_path_invalid" });
    }
    const content = Buffer.from(String(item.content_base64 || ""), "base64");
    if (item.sha256 && digest(content) !== item.sha256) {
      throw new RuntimeError("资产文件 digest 不匹配", { code: "asset_digest_mismatch" });
    }
    if (typeof item.size === "number" && content.length !== item.size) {
      throw new RuntimeError("资产文件大小不匹配", { code: "asset_size_mismatch" });
    }
    files[item.path] = content;
    digests[item.path] = digest(content);
    modes[item.path] = typeof item.mode === "string" ? item.mode : "100644";
  }
  if (assetSnapshot.tree_digest) {
    const treeDigest = digest(
      Object.keys(files)
        .sort()
        .map((path) => `${path}\0${digests[path]}`)
        .join("\n"),
    );
    if (treeDigest !== assetSnapshot.tree_digest) {
      throw new RuntimeError("资产树 digest 不匹配", { code: "asset_digest_mismatch" });
    }
  }
  return { files, modes };
}

export function parseAssetBundle(bytes, assetSnapshot) {
  return parseAssetBundleDetailed(bytes, assetSnapshot).files;
}

/**
 * Replace the workspace `.claude` asset roots with exactly the snapshot files.
 * Returns the written paths. A snapshot without assets means "no injection";
 * declared assets missing snapshot_id/bundle metadata fail explicitly.
 */
export async function materializeSnapshot(assetSnapshot, workspace, options = {}) {
  const assetCount = Number(assetSnapshot?.asset_count || 0);
  if (!assetSnapshot || assetCount === 0) {
    return [];
  }
  if (!assetSnapshot.snapshot_id) {
    // Never silently drop declared assets: a missing snapshot_id means the
    // frozen metadata is incomplete (or the task payload was trimmed).
    throw new RuntimeError("资产快照缺少 snapshot_id", { code: "asset_snapshot_missing" });
  }
  const bytes = await downloadAssetBundle(assetSnapshot, options);
  const { files, modes } = parseAssetBundleDetailed(bytes, assetSnapshot);
  const root = resolve(workspace, ".claude");
  // Remove the repository's own .claude so only the frozen snapshot is loaded.
  // Dispatch workloads must not inherit host or repository asset definitions.
  rmSync(root, { recursive: true, force: true });
  mkdirSync(root, { recursive: true });
  const written = materializeAssets(files, workspace);
  for (const path of Object.keys(files)) {
    chmodSync(join(root, ...path.split("/")), modes[path] === "100755" ? 0o755 : 0o644);
  }
  return written;
}

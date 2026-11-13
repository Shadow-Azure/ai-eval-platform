/**
 * M8-22: rebuild CLI-generated Agent assets from the selected ref.
 *
 * CLI-generated sources (OpenSpec/CodeSpec) have no committed `.claude` tree:
 * their install command builds the CLI from the asset repository and their init
 * command generates Skills/Commands/Subagents. The platform therefore freezes
 * the resolved commit as a source archive and publishes a short-lived presigned
 * URL. Downloading and extracting that archive is the evaluation-time
 * equivalent of `git clone` + `git checkout <commit>` — without handing the
 * runner host a long-lived Git credential.
 *
 * Static assets that the repository already contains are materialized from the
 * same archive (verified against the frozen digests); generated assets are
 * rebuilt by the init command inside the evaluation workspace.
 */
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";

import { RuntimeError } from "./runtime-claude-code.mjs";

const MAX_ARCHIVE_ENTRIES = 50_000;
const MAX_ARCHIVE_EXTRACT_BYTES = 512 * 1024 * 1024;
const TAR_TIMEOUT_MS = 120_000;

function digest(bytes) {
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

function safeRelative(path) {
  if (typeof path !== "string" || !path || path.includes("\0") || path.includes("\\")) return null;
  if (path.startsWith("/")) return null;
  const parts = path.split("/");
  if (parts.some((part) => part === "" || part === "." || part === "..")) return null;
  return parts;
}

function inside(root, candidate) {
  const rel = relative(root, candidate);
  return rel === "" || (!rel.startsWith("..") && !rel.includes(`..${sep}`));
}

function treeBytes(root) {
  let total = 0;
  for (const name of readdirSync(root)) {
    const path = join(root, name);
    const info = lstatSync(path);
    if (info.isSymbolicLink()) continue;
    total += info.isDirectory() ? treeBytes(path) : info.size;
  }
  return total;
}

/** Download the pinned commit archive and verify its platform digest. */
export async function downloadSourceArchive(assetSnapshot, { fetchImpl = fetch } = {}) {
  const url = assetSnapshot?.source_archive_url;
  if (!url) {
    throw new RuntimeError("源码包缺少 source_archive_url", { code: "source_archive_missing" });
  }
  let bytes;
  if (String(url).startsWith("file://")) {
    bytes = readFileSync(new URL(url));
  } else {
    const response = await fetchImpl(url, { signal: AbortSignal.timeout(120_000) });
    if (!response.ok) {
      throw new RuntimeError(`源码包下载失败：HTTP ${response.status}`, {
        code: "source_archive_unavailable",
      });
    }
    bytes = Buffer.from(await response.arrayBuffer());
  }
  const expected = String(assetSnapshot?.source_archive_digest || "");
  if (expected && digest(bytes) !== expected) {
    throw new RuntimeError("源码包 digest 不匹配", { code: "source_archive_digest_mismatch" });
  }
  return bytes;
}

/**
 * Extract a GitHub commit tarball into ``destination``. Every entry must be a
 * safe relative path; the archive's single top-level ``owner-repo-sha/``
 * wrapper is stripped so the result is a repository checkout.
 */
export function extractSourceArchive(bytes, destination) {
  mkdirSync(destination, { recursive: true });
  const archivePath = join(destination, "source.tar.gz");
  writeFileSync(archivePath, bytes);
  try {
    const listed = spawnSync("tar", ["-tzf", archivePath], {
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
      timeout: TAR_TIMEOUT_MS,
    });
    if (listed.error || listed.status !== 0) {
      throw new RuntimeError(
        `源码包无法读取：${listed.error?.message || (listed.stderr || "").slice(-400)}`,
        { code: "source_archive_invalid" },
      );
    }
    const entries = String(listed.stdout || "")
      .split("\n")
      .map((entry) => entry.trim())
      .filter(Boolean);
    if (entries.length > MAX_ARCHIVE_ENTRIES) {
      throw new RuntimeError(`源码包条目 ${entries.length} 超过上限 ${MAX_ARCHIVE_ENTRIES}`, {
        code: "source_archive_too_large",
      });
    }
    for (const entry of entries) {
      let normalized = entry;
      while (normalized.startsWith("./")) normalized = normalized.slice(2);
      // Directory entries end with "/" in tar listings; validate the path
      // without that marker so a legitimately nested directory is not rejected.
      normalized = normalized.replace(/\/+$/, "");
      if (!normalized || normalized === ".") continue;
      if (!safeRelative(normalized)) {
        throw new RuntimeError(`源码包路径非法：${entry}`, { code: "source_archive_invalid" });
      }
    }
    const extracted = spawnSync(
      "tar",
      ["-xzf", archivePath, "-C", destination, "--strip-components=1"],
      {
        encoding: "utf8",
        maxBuffer: 16 * 1024 * 1024,
        timeout: TAR_TIMEOUT_MS,
      },
    );
    if (extracted.error || extracted.status !== 0) {
      throw new RuntimeError(
        `源码包解压失败：${extracted.error?.message || (extracted.stderr || "").slice(-400)}`,
        { code: "source_archive_invalid" },
      );
    }
    if (treeBytes(destination) > MAX_ARCHIVE_EXTRACT_BYTES) {
      throw new RuntimeError("源码包解压后超过大小上限", { code: "source_archive_too_large" });
    }
    return destination;
  } catch (error) {
    rmSync(destination, { recursive: true, force: true });
    throw error;
  } finally {
    rmSync(archivePath, { force: true });
  }
}

function writeVerified(sourceRoot, sourcePath, destinationRoot, destinationPath, expectedSha) {
  const sourceParts = safeRelative(sourcePath);
  const destinationParts = safeRelative(destinationPath);
  if (!sourceParts || !destinationParts) {
    throw new RuntimeError(`资产路径非法：${sourcePath} → ${destinationPath}`, {
      code: "asset_path_invalid",
    });
  }
  const source = resolve(sourceRoot, ...sourceParts);
  const destination = resolve(destinationRoot, ...destinationParts);
  if (!inside(sourceRoot, source)) {
    throw new RuntimeError(`资产源路径越界：${sourcePath}`, { code: "asset_path_invalid" });
  }
  if (!inside(destinationRoot, destination)) {
    throw new RuntimeError(`资产目标路径越界：${destinationPath}`, { code: "asset_path_invalid" });
  }
  if (!existsSync(source) || !lstatSync(source).isFile()) {
    // A symlink is rejected: the archive is administrator-controlled, but a
    // static asset must be a real file so it cannot escape the checkout.
    throw new RuntimeError(`源码包缺少选中资产：${sourcePath}`, {
      code: "source_archive_asset_missing",
    });
  }
  const content = readFileSync(source);
  if (expectedSha && digest(content) !== expectedSha) {
    throw new RuntimeError(`源码资产 digest 不匹配：${sourcePath}`, {
      code: "source_archive_asset_mismatch",
    });
  }
  mkdirSync(dirname(destination), { recursive: true });
  writeFileSync(destination, content);
  return destination;
}

/**
 * Materialize the selected static assets from the checkout. Generated assets
 * are skipped: the init command rebuilds them in the evaluation workspace.
 */
export function injectSourceAssets(assetSnapshot, sourceRoot, workspace) {
  const assets = Array.isArray(assetSnapshot?.assets) ? assetSnapshot.assets : [];
  const claudeRoot = resolve(workspace, ".claude");
  mkdirSync(claudeRoot, { recursive: true });
  let written = 0;
  for (const asset of assets) {
    if (!asset || asset.metadata?.generated) continue;
    const type = asset.asset_type;
    if (!["skill", "command", "subagent"].includes(type)) continue;
    if (type === "skill") {
      const prefix = `skills/${asset.name}/`;
      const files = Array.isArray(asset.metadata?.files) ? asset.metadata.files : [];
      for (const file of files) {
        const relativePath = String(file.path || "");
        if (!relativePath.startsWith(prefix)) continue;
        const sourceRelative = `${asset.path}/${relativePath.slice(prefix.length)}`;
        writeVerified(sourceRoot, sourceRelative, claudeRoot, relativePath, file.sha256);
        written += 1;
      }
      continue;
    }
    const destination =
      asset.metadata?.runtime_path ||
      (type === "command" ? `commands/${asset.name}.md` : `agents/${asset.name}.md`);
    const expected = Array.isArray(asset.metadata?.files)
      ? asset.metadata.files[0]?.sha256
      : null;
    writeVerified(sourceRoot, asset.path, claudeRoot, destination, expected);
    written += 1;
  }
  return written;
}

function expectedPaths(asset) {
  if (!asset) return [];
  if (asset.asset_type === "skill") {
    return [`.claude/skills/${asset.name}/SKILL.md`];
  }
  const runtimePath =
    asset.metadata?.runtime_path ||
    (asset.asset_type === "command"
      ? `commands/${asset.name}.md`
      : `agents/${asset.name}.md`);
  return [`.claude/${runtimePath}`];
}

/** Return the selected assets whose runtime file is still missing after init. */
export function missingSourceAssets(assetSnapshot, workspace) {
  const root = resolve(workspace);
  const missing = [];
  for (const asset of Array.isArray(assetSnapshot?.assets) ? assetSnapshot.assets : []) {
    for (const path of expectedPaths(asset)) {
      const parts = safeRelative(path);
      const absolute = parts ? resolve(root, ...parts) : null;
      if (!absolute || !inside(root, absolute) || !existsSync(absolute)) {
        missing.push(path);
      }
    }
  }
  return [...new Set(missing)];
}

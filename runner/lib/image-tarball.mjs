/**
 * OSS image tarball loading for cold spot runners (issue #273).
 *
 * A cold spot instance has no Docker cache and the public mirrors are too slow
 * for multi-GB SWE-bench images. The platform presigns a short-lived internal
 * OSS GET URL for the image's deterministic tarball key; this module downloads
 * that tarball, loads it into Docker, verifies the expected image and reports
 * a structured trace entry. Any failure returns ``ok: false`` so the caller
 * can fall back to the existing public registry chain.
 *
 * The signed URL is never written to logs or diagnostics: only the object key
 * and host are kept.
 */

import { spawnSync as nodeSpawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const DEFAULT_DOWNLOAD_TIMEOUT_MS = 900_000;
const DEFAULT_LOAD_TIMEOUT_MS = 900_000;
const INSPECT_TIMEOUT_MS = 60_000;

function urlHost(value) {
  try {
    return new URL(value).host;
  } catch {
    return null;
  }
}

function truncated(value, limit = 400) {
  const text = String(value ?? "").trim();
  return text.length > limit ? text.slice(-limit) : text;
}

function scrubUrl(value, url) {
  const text = truncated(value);
  return url ? text.split(url).join("<tarball-url>") : text;
}

function recordResult(traceDir, result) {
  if (!traceDir) return;
  try {
    writeFileSync(join(traceDir, "image-tarball.json"), `${JSON.stringify(result, null, 2)}\n`);
  } catch {
    // Diagnostics must never fail the case; the caller still gets the result.
  }
}

/**
 * Download, load and verify one image tarball.
 *
 * @param {{url?: string, key?: string, image?: string, format?: string}} tarball
 * @param {{traceDir?: string|null, spawn?: Function, downloadTimeoutMs?: number, loadTimeoutMs?: number}} options
 * @returns {{ok: boolean, image: string|null, key: string|null, stage: string, elapsed_ms: number, error: string|null}}
 */
export function loadImageTarball(tarball, options = {}) {
  const spawn = options.spawn ?? nodeSpawnSync;
  const downloadTimeoutMs = options.downloadTimeoutMs ?? DEFAULT_DOWNLOAD_TIMEOUT_MS;
  const loadTimeoutMs = options.loadTimeoutMs ?? DEFAULT_LOAD_TIMEOUT_MS;
  const traceDir = options.traceDir ?? null;
  const startedAt = Date.now();
  const result = {
    ok: false,
    image: null,
    key: typeof tarball?.key === "string" ? tarball.key : null,
    host: urlHost(tarball?.url),
    stage: "start",
    elapsed_ms: 0,
    error: null,
  };
  const finish = (stage, error = null) => {
    result.stage = stage;
    result.error = error;
    result.elapsed_ms = Date.now() - startedAt;
    recordResult(traceDir, result);
    return result;
  };

  if (!tarball || typeof tarball.url !== "string" || !/^https?:\/\//i.test(tarball.url)) {
    return finish("invalid", "image tarball URL 缺失或不是 http(s)");
  }
  if (!tarball.image) return finish("invalid", "image tarball 缺少镜像引用");

  // A fixed runner (or a warm spot instance) already has the image; never
  // download the tarball just to load an identical image. The fixed pool is
  // the common case and must stay on its local cache.
  try {
    const cached = spawn("docker", ["image", "inspect", tarball.image], {
      encoding: "utf8",
      timeout: INSPECT_TIMEOUT_MS,
      maxBuffer: 8 * 1024 * 1024,
    });
    if (cached?.status === 0) {
      result.ok = true;
      result.image = tarball.image;
      return finish("cached");
    }
  } catch {
    // Docker unavailable/cached inspect failed: continue with the download.
  }

  const dir = mkdtempSync(join(tmpdir(), "eval-image-tarball-"));
  const tarPath = join(dir, "image.tar.gz");
  try {
    result.stage = "download";
    const download = spawn(
      "curl",
      [
        "-fsSL",
        "--max-time",
        String(Math.ceil(downloadTimeoutMs / 1000)),
        "-o",
        tarPath,
        tarball.url,
      ],
      { encoding: "utf8", timeout: downloadTimeoutMs },
    );
    if (download?.error || download?.status !== 0) {
      return finish(
        "download",
        scrubUrl(download?.stderr || download?.error?.message || "curl 下载失败", tarball.url),
      );
    }

    result.stage = "load";
    const load = spawn("docker", ["load", "-i", tarPath], {
      encoding: "utf8",
      timeout: loadTimeoutMs,
      maxBuffer: 64 * 1024 * 1024,
    });
    if (load?.error || load?.status !== 0) {
      return finish(
        "load",
        scrubUrl(load?.stderr || load?.error?.message || "docker load 失败", tarball.url),
      );
    }

    result.stage = "verify";
    const inspect = spawn("docker", ["image", "inspect", tarball.image], {
      encoding: "utf8",
      timeout: INSPECT_TIMEOUT_MS,
      maxBuffer: 8 * 1024 * 1024,
    });
    if (inspect?.error || inspect?.status !== 0) {
      return finish(
        "verify",
        scrubUrl(inspect?.stderr || inspect?.error?.message || "docker image inspect 失败", tarball.url),
      );
    }

    result.ok = true;
    result.image = tarball.image;
    return finish("loaded");
  } catch (error) {
    return finish(result.stage, scrubUrl(error?.message || error, tarball.url));
  } finally {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      // Best-effort cleanup; a leftover temp dir must not mask the result.
    }
  }
}

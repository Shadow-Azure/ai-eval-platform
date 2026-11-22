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
 *
 * Issue #371: the URL is requested only after capacity admission, and a 401/403
 * on the first download can be repaired by refreshing the per-case `/tasks`
 * descriptor once. `loadImageTarballWithRefresh` owns that one retry so the
 * caller cannot accidentally loop on a stale credential.
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { runCommand } from "./run-command.mjs";

const DEFAULT_DOWNLOAD_TIMEOUT_MS = 900_000;
const DEFAULT_LOAD_TIMEOUT_MS = 900_000;
const INSPECT_TIMEOUT_MS = 60_000;
// A rejected presigned GET (expired credential, revoked signer) is the only
// failure a fresh `/tasks` response can fix; 404/timeout/corrupt tar must fall
// back to the registry chain immediately.
const AUTH_HTTP_STATUSES = new Set([401, 403]);

/** Parse curl's `--write-out %{http_code}` trailer without trusting stderr. */
function parseHttpStatus(stdout) {
  const match = /(\d{3})\s*$/.exec(String(stdout ?? "").trim());
  if (!match) return null;
  const status = Number(match[1]);
  return status >= 100 && status <= 599 ? status : null;
}

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
 * #363: the download and `docker load` run through async `spawn` so a cold
 * runner keeps processing network I/O while a multi-GB tarball loads. The
 * failure stages, diagnostics file and fallback contract are unchanged.
 *
 * @param {{url?: string, key?: string, image?: string, format?: string}} tarball
 * @param {{traceDir?: string|null, spawn?: Function, downloadTimeoutMs?: number, loadTimeoutMs?: number}} options
 * @returns {Promise<{ok: boolean, image: string|null, key: string|null, stage: string, httpStatus: number|null, elapsed_ms: number, error: string|null}>}
 */
export async function loadImageTarball(tarball, options = {}) {
  const spawn = options.spawn;
  const downloadTimeoutMs = options.downloadTimeoutMs ?? DEFAULT_DOWNLOAD_TIMEOUT_MS;
  const loadTimeoutMs = options.loadTimeoutMs ?? DEFAULT_LOAD_TIMEOUT_MS;
  const traceDir = options.traceDir ?? null;
  const run = (command, args, runOptions) => runCommand(command, args, { spawn, ...runOptions });
  const startedAt = Date.now();
  const result = {
    ok: false,
    image: null,
    key: typeof tarball?.key === "string" ? tarball.key : null,
    host: urlHost(tarball?.url),
    stage: "start",
    httpStatus: null,
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
    const cached = await run("docker", ["image", "inspect", tarball.image], {
      timeout: INSPECT_TIMEOUT_MS,
      maxBuffer: 8 * 1024 * 1024,
    });
    if (cached?.status === 0 && !cached.error) {
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
    const download = await run(
      "curl",
      [
        "-fsSL",
        "--max-time",
        String(Math.ceil(downloadTimeoutMs / 1000)),
        "--write-out",
        "%{http_code}",
        "-o",
        tarPath,
        tarball.url,
      ],
      { timeout: downloadTimeoutMs },
    );
    result.httpStatus = parseHttpStatus(download?.stdout);
    if (download?.error || download?.status !== 0) {
      return finish(
        "download",
        scrubUrl(download?.stderr || download?.error?.message || "curl 下载失败", tarball.url),
      );
    }

    result.stage = "load";
    const load = await run("docker", ["load", "-i", tarPath], {
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
    const inspect = await run("docker", ["image", "inspect", tarball.image], {
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

/** The first download was rejected by the object store's credential check. */
export function isTarballAuthFailure(result) {
  return Boolean(
    result &&
      result.ok === false &&
      result.stage === "download" &&
      AUTH_HTTP_STATUSES.has(result.httpStatus),
  );
}

/**
 * Load a tarball, refreshing the descriptor once on an auth failure (#371).
 *
 * `refresh` returns a fresh per-case descriptor (a new presigned URL) or
 * `null`. Only 401/403 triggers it; a 404, timeout, corrupt tar or load failure
 * falls back to the registry chain exactly as before. A refresh that returns no
 * URL, the same URL or throws leaves the original failure untouched, so the
 * caller still reports the real download error.
 *
 * @param {{url?: string, key?: string, image?: string, format?: string}} tarball
 * @param {(() => Promise<{url?: string, key?: string, image?: string, format?: string}|null>)|null} refresh
 * @param {object} options
 * @returns {Promise<object>} result of the first or refreshed attempt
 */
export async function loadImageTarballWithRefresh(tarball, refresh, options = {}) {
  const first = await loadImageTarball(tarball, options);
  first.refreshed = false;
  if (first.ok || !isTarballAuthFailure(first) || typeof refresh !== "function") {
    return first;
  }
  let refreshed;
  try {
    refreshed = await refresh();
  } catch (error) {
    first.refreshError = truncated(error?.message || error, 300);
    return first;
  }
  if (!refreshed?.url || refreshed.url === tarball?.url) return first;
  const second = await loadImageTarball(refreshed, options);
  second.refreshed = true;
  return second;
}

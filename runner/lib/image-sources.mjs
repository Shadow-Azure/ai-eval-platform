/**
 * M8-33: ordered image references for runtime pull fallback.
 *
 * Dataset cases normally carry a Docker Hub reference such as
 * `swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest`. A cold spot
 * runner has no local copy and Docker Hub may be unreachable. The platform
 * freezes its enabled public registry sources into the shard manifest; this
 * helper turns one image into the ordered list of refs to try.
 *
 * Pure module: no Docker/network access, so it is unit-testable.
 */

function trimSlashes(value) {
  return String(value || "").replace(/^\/+|\/+$/g, "");
}

function splitReference(image) {
  const raw = String(image || "").trim();
  if (!raw) return { repository: "", suffix: "" };
  const digestAt = raw.indexOf("@");
  if (digestAt >= 0) {
    return { repository: raw.slice(0, digestAt), suffix: raw.slice(digestAt) };
  }
  const lastSlash = raw.lastIndexOf("/");
  const lastColon = raw.lastIndexOf(":");
  if (lastColon > lastSlash) {
    return { repository: raw.slice(0, lastColon), suffix: raw.slice(lastColon) };
  }
  return { repository: raw, suffix: "" };
}

function normalizeRegistry(registry) {
  return trimSlashes(String(registry || "").replace(/^https?:\/\//i, ""));
}

/** True when the first path component is a registry host (not a Docker Hub namespace). */
function hasExplicitRegistry(repository) {
  const first = repository.split("/")[0] || "";
  return first.includes(".") || first.includes(":") || first === "localhost";
}

function canonicalRepository(repository) {
  return repository.replace(/^docker\.io\//, "").replace(/^index\.docker\.io\//, "");
}

/**
 * Build ordered unique refs for one image.
 * - explicit registry refs are tried as-is first;
 * - every enabled source is tried in order with `registry[/prefix]/<repository>`;
 * - the untouched original is kept as a final fallback when it was not already
 *   expressed by a source (e.g. no sources configured).
 */
export function imageCandidates(image, sources = []) {
  const raw = String(image || "").trim();
  if (!raw) return [];
  const { repository, suffix } = splitReference(raw);
  if (!repository) return [];
  const explicit = hasExplicitRegistry(repository);
  const base = explicit ? repository : canonicalRepository(repository);
  const refs = [];
  const seen = new Set();
  const push = (ref) => {
    const value = String(ref || "").trim();
    if (!value || seen.has(value)) return;
    seen.add(value);
    refs.push(value);
  };

  if (explicit) push(raw);

  for (const source of Array.isArray(sources) ? sources : []) {
    if (!source || source.enabled === false) continue;
    const registry = normalizeRegistry(source.registry);
    if (!registry) continue;
    const prefix = trimSlashes(source.prefix);
    const path = prefix ? `${prefix}/${base}` : base;
    push(`${registry}/${path}${suffix}`);
  }

  // Docker Hub (or the explicit default) remains the last fallback.
  push(`${base}${suffix}`);
  return refs;
}

export function sourceLabel(candidate, sources = []) {
  const value = String(candidate || "");
  for (const source of Array.isArray(sources) ? sources : []) {
    const registry = normalizeRegistry(source.registry);
    if (registry && value.startsWith(`${registry}/`)) return source.name || registry;
  }
  return "default";
}

import { ApiError } from "./types";
import { request } from "./evaluations";

export type AssetType = "skill" | "command" | "subagent";

export interface AssetMetadata {
  description?: string;
  display_name?: string;
  runtime_path?: string;
  prerequisites?: string[];
  file_count?: number;
  size?: number;
  /** True when the CLI load test produced the asset instead of the Git tree. */
  generated?: boolean;
  files?: Array<{ path: string; sha256: string; size: number }>;
  [key: string]: unknown;
}

export interface ScannedAsset {
  path: string;
  asset_type: AssetType;
  name: string;
  content_digest: string;
  metadata: AssetMetadata;
}

export interface SnapshotAsset extends ScannedAsset {
  id?: string;
  snapshot_id?: string;
  ordinal?: number;
}

export interface AssetScan {
  source_id: string | null;
  requested_ref: string;
  resolved_commit: string;
  assets: ScannedAsset[];
  unclassified: Array<{ path: string; size: number }>;
  counts: Record<AssetType, number>;
  warnings: string[];
  scanned_at?: string | null;
}

export interface RuntimePrerequisites {
  install_command?: string;
  init_command?: string;
  runtime?: string;
}

export type LoadTestStatus = "untested" | "testing" | "passed" | "failed";

export interface GeneratedAssetCounts {
  skill: number;
  command: number;
  subagent: number;
}

/**
 * Load-test document stored on an asset source and returned by the preview
 * and source-scoped load-test endpoints. ``generated`` counts the assets the
 * CLI produced; ``assets`` is the generated inventory when the backend
 * includes it (used to seed the add-source wizard selection).
 */
export interface AssetLoadTest {
  status: LoadTestStatus;
  duration_seconds: number | null;
  generated: GeneratedAssetCounts;
  installed: GeneratedAssetCounts;
  assets: ScannedAsset[];
  log_tail: string | null;
  install_command: string | null;
  init_command: string | null;
  tested_at: string | null;
  /** M8-22: the checkout the load test ran in. */
  repo_url?: string;
  requested_ref?: string;
  resolved_commit?: string;
}

export interface AssetLoadTestPreview extends AssetLoadTest {
  load_test_token: string | null;
}

export interface AssetSource {
  id: string;
  name: string;
  description: string | null;
  repo_url: string;
  provider: string;
  default_ref: string;
  scan_paths: string[];
  selection_mode: "directory" | "explicit";
  status: string;
  runtime_prerequisites: RuntimePrerequisites;
  load_test: AssetLoadTest;
  last_scanned_commit: string | null;
  last_scan: Partial<AssetScan> & { assets?: ScannedAsset[]; counts?: Record<AssetType, number> };
  counts: Record<AssetType, number>;
  credential_mask: string | null;
  uses_platform_token: boolean;
  latest_snapshot_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssetSnapshot {
  id: string;
  source_id: string;
  requested_ref: string;
  resolved_commit: string;
  selection_mode: string;
  selection: Array<{ asset_type: AssetType; path: string; name: string }>;
  tree_digest: string;
  bundle_key: string;
  bundle_digest: string;
  bundle_size: number;
  file_count: number;
  asset_count: number;
  runtime_key: string;
  created_by: string;
  created_at: string;
  assets: SnapshotAsset[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function parseCounts(value: unknown): Record<AssetType, number> {
  const record = isRecord(value) ? value : {};
  return {
    skill: count(record.skill),
    command: count(record.command),
    subagent: count(record.subagent),
  };
}

function parseAssetType(value: unknown): AssetType {
  return value === "command" || value === "subagent" ? value : "skill";
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readCount(record: Record<string, unknown>, keys: string[]): number {
  for (const key of keys) {
    if (typeof record[key] === "number") return count(record[key]);
  }
  return 0;
}

export function countAssetTypes(assets: ScannedAsset[]): GeneratedAssetCounts {
  const result: GeneratedAssetCounts = { skill: 0, command: 0, subagent: 0 };
  for (const asset of assets) result[asset.asset_type] += 1;
  return result;
}

/**
 * Accept the canonical ``generated: {skill, command, subagent}`` counts as
 * well as the prototype-style plural keys and a generated-assets inventory.
 */
function parseGeneratedCounts(value: unknown, fallbackAssets: ScannedAsset[]): GeneratedAssetCounts {
  if (Array.isArray(value)) {
    return countAssetTypes(value.filter(isRecord).map(parseAsset));
  }
  if (isRecord(value)) {
    if (isRecord(value.counts)) return parseGeneratedCounts(value.counts, fallbackAssets);
    const hasCountKeys = [
      "skill",
      "skills",
      "command",
      "commands",
      "subagent",
      "subagents",
      "agent",
      "agents",
    ].some((key) => key in value);
    if (hasCountKeys) {
      return {
        skill: readCount(value, ["skill", "skills"]),
        command: readCount(value, ["command", "commands"]),
        subagent: readCount(value, ["subagent", "subagents", "agent", "agents"]),
      };
    }
    if (Array.isArray(value.assets)) {
      return countAssetTypes(value.assets.filter(isRecord).map(parseAsset));
    }
  }
  return countAssetTypes(fallbackAssets);
}

function parseLoadTestStatus(value: unknown): LoadTestStatus {
  const status = text(value).toLowerCase();
  if (status === "passed" || status === "success" || status === "ok" || status === "ready") return "passed";
  if (status === "failed" || status === "error") return "failed";
  if (status === "testing" || status === "running") return "testing";
  return "untested";
}

function parseLogTail(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (Array.isArray(value)) {
    const lines = value.filter((item): item is string => typeof item === "string");
    return lines.length ? lines.join("\n") : null;
  }
  return null;
}

function parseRuntimePrerequisites(value: unknown): RuntimePrerequisites {
  const record = isRecord(value) ? value : {};
  return {
    install_command: optionalText(record.install_command) ?? undefined,
    init_command: optionalText(record.init_command) ?? undefined,
    runtime: optionalText(record.runtime) ?? undefined,
  };
}

function parseLoadTest(raw: unknown): AssetLoadTest {
  const record = isRecord(raw) ? raw : {};
  const assets = Array.isArray(record.assets)
    ? record.assets.filter(isRecord).map(parseAsset).filter((asset) => Boolean(asset.path))
    : [];
  const generated = parseGeneratedCounts(record.generated, assets);
  return {
    status: parseLoadTestStatus(record.status),
    duration_seconds: optionalNumber(record.duration_seconds),
    generated,
    installed:
      record.installed === undefined ? generated : parseGeneratedCounts(record.installed, assets),
    assets,
    log_tail: parseLogTail(record.log_tail),
    install_command: optionalText(record.install_command),
    init_command: optionalText(record.init_command),
    tested_at: optionalText(record.tested_at),
    repo_url: optionalText(record.repo_url) ?? undefined,
    requested_ref: optionalText(record.requested_ref) ?? undefined,
    resolved_commit: optionalText(record.resolved_commit) ?? undefined,
  };
}

function loadTestDocument(raw: Record<string, unknown>): Record<string, unknown> {
  return isRecord(raw.load_test) ? { ...raw, ...raw.load_test } : raw;
}

function parseAsset(raw: unknown): ScannedAsset {
  if (!isRecord(raw)) throw new ApiError("资产条目格式无效", { code: "invalid_response" });
  const metadata = isRecord(raw.metadata) ? (raw.metadata as AssetMetadata) : {};
  // The tree endpoint stamps load-test generated entries with
  // ``metadata.generated``; accept a top-level flag as well so a response that
  // carries it elsewhere still renders the "生成" tag.
  const generated = metadata.generated === true || raw.generated === true;
  return {
    path: text(raw.path),
    asset_type: parseAssetType(raw.asset_type),
    name: text(raw.name, text(raw.path)),
    content_digest: text(raw.content_digest),
    metadata: generated ? { ...metadata, generated: true } : metadata,
  };
}

/**
 * True when the asset came from the source's CLI load test rather than from
 * the Git tree. ``generatedPaths`` lets callers treat the paths listed by a
 * passed load test as generated even before the tree endpoint stamps them.
 */
export function isGeneratedAsset(
  asset: ScannedAsset,
  generatedPaths?: ReadonlySet<string>,
): boolean {
  if (asset.metadata.generated === true) return true;
  return generatedPaths ? generatedPaths.has(asset.path) : false;
}

/** Paths of the assets a passed load test generated; empty otherwise. */
export function generatedPathsOf(loadTest: AssetLoadTest | null | undefined): Set<string> {
  const paths = new Set<string>();
  if (loadTest?.status !== "passed") return paths;
  for (const asset of loadTest.assets) {
    if (asset.path) paths.add(asset.path);
  }
  return paths;
}

/**
 * Merge the load test's generated inventory into tree entries. The tree
 * endpoint returns generated assets directly once the control plane includes
 * them; this fallback keeps the selection usable when only the static Git scan
 * is returned, without bypassing the resolve/snapshot flow.
 */
export function mergeGeneratedAssets(
  assets: ScannedAsset[],
  loadTest: AssetLoadTest | null | undefined,
): { assets: ScannedAsset[]; supplemented: ScannedAsset[] } {
  if (loadTest?.status !== "passed") return { assets, supplemented: [] };
  const seen = new Set(assets.map((asset) => asset.path));
  const supplemented: ScannedAsset[] = [];
  for (const asset of loadTest.assets) {
    if (!asset.path || seen.has(asset.path)) continue;
    seen.add(asset.path);
    supplemented.push({
      ...asset,
      metadata: { ...asset.metadata, generated: true, prerequisites: [] },
    });
  }
  return {
    assets: supplemented.length ? [...assets, ...supplemented] : assets,
    supplemented,
  };
}

function parseScan(raw: unknown): AssetScan {
  if (!isRecord(raw)) throw new ApiError("资产扫描结果格式无效", { code: "invalid_response" });
  return {
    source_id: optionalText(raw.source_id),
    requested_ref: text(raw.requested_ref),
    resolved_commit: text(raw.resolved_commit),
    assets: Array.isArray(raw.assets) ? raw.assets.map(parseAsset) : [],
    unclassified: Array.isArray(raw.unclassified)
      ? raw.unclassified
          .filter(isRecord)
          .map((item) => ({ path: text(item.path), size: count(item.size) }))
      : [],
    counts: parseCounts(raw.counts),
    warnings: Array.isArray(raw.warnings) ? raw.warnings.filter((item): item is string => typeof item === "string") : [],
    scanned_at: optionalText(raw.scanned_at),
  };
}

function parseSource(raw: unknown): AssetSource {
  if (!isRecord(raw)) throw new ApiError("资产源格式无效", { code: "invalid_response" });
  const lastScan = isRecord(raw.last_scan) ? raw.last_scan : {};
  const runtimeRecord = isRecord(raw.runtime_prerequisites) ? raw.runtime_prerequisites : {};
  return {
    id: text(raw.id),
    name: text(raw.name),
    description: optionalText(raw.description),
    repo_url: text(raw.repo_url),
    provider: text(raw.provider, "github"),
    default_ref: text(raw.default_ref),
    scan_paths: Array.isArray(raw.scan_paths) ? raw.scan_paths.filter((item): item is string => typeof item === "string") : [],
    selection_mode: raw.selection_mode === "explicit" ? "explicit" : "directory",
    status: text(raw.status, "unverified"),
    runtime_prerequisites: parseRuntimePrerequisites(runtimeRecord),
    load_test: parseLoadTest(raw.load_test ?? runtimeRecord.load_test),
    last_scanned_commit: optionalText(raw.last_scanned_commit),
    last_scan: {
      ...lastScan,
      assets: Array.isArray(lastScan.assets) ? lastScan.assets.map(parseAsset) : [],
      counts: parseCounts(lastScan.counts),
    },
    counts: parseCounts(raw.counts),
    credential_mask: optionalText(raw.credential_mask),
    uses_platform_token: Boolean(raw.uses_platform_token),
    latest_snapshot_id: optionalText(raw.latest_snapshot_id),
    created_at: text(raw.created_at),
    updated_at: text(raw.updated_at),
  };
}

function parseSnapshot(raw: unknown): AssetSnapshot {
  if (!isRecord(raw)) throw new ApiError("资产快照格式无效", { code: "invalid_response" });
  return {
    id: text(raw.id),
    source_id: text(raw.source_id),
    requested_ref: text(raw.requested_ref),
    resolved_commit: text(raw.resolved_commit),
    selection_mode: text(raw.selection_mode, "explicit"),
    selection: Array.isArray(raw.selection)
      ? raw.selection.filter(isRecord).map((item) => ({
          asset_type: parseAssetType(item.asset_type),
          path: text(item.path),
          name: text(item.name),
        }))
      : [],
    tree_digest: text(raw.tree_digest),
    bundle_key: text(raw.bundle_key),
    bundle_digest: text(raw.bundle_digest),
    bundle_size: count(raw.bundle_size),
    file_count: count(raw.file_count),
    asset_count: count(raw.asset_count),
    runtime_key: text(raw.runtime_key, "claude-code"),
    created_by: text(raw.created_by),
    created_at: text(raw.created_at),
    assets: Array.isArray(raw.assets) ? raw.assets.map(parseAsset) : [],
  };
}

export async function fetchAssetSources(signal?: AbortSignal): Promise<AssetSource[]> {
  const payload = await request("/api/v1/agent-asset-sources", { signal });
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的资产源数据", { code: "invalid_response" });
  }
  return payload.items.map(parseSource);
}

export async function createAssetSource(body: {
  name: string;
  repo_url: string;
  default_ref: string;
  scan_paths: string[];
  selection_mode: "directory" | "explicit";
  description?: string;
  access_token?: string;
  runtime_prerequisites?: RuntimePrerequisites;
  /** Preview token returned by ``previewAssetLoadTest`` for a passed result. */
  load_test_token?: string;
}): Promise<AssetSource> {
  return parseSource(
    await request("/api/v1/agent-asset-sources", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function previewAssetScan(body: {
  repo_url: string;
  requested_ref: string;
  scan_paths: string[];
  access_token?: string;
}): Promise<AssetScan> {
  return parseScan(
    await request("/api/v1/agent-asset-sources/scan-preview", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function previewAssetLoadTest(body: {
  repo_url: string;
  requested_ref: string;
  scan_paths: string[];
  runtime_prerequisites: RuntimePrerequisites;
  access_token?: string;
}): Promise<AssetLoadTestPreview> {
  const payload = await request("/api/v1/agent-asset-sources/load-test-preview", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!isRecord(payload)) throw new ApiError("加载测试结果格式无效", { code: "invalid_response" });
  const document = loadTestDocument(payload);
  return {
    ...parseLoadTest(document),
    load_test_token:
      optionalText(payload.load_test_token) ?? optionalText(document.load_test_token),
  };
}

export async function loadTestAssetSource(
  sourceId: string,
): Promise<{ source: AssetSource | null; loadTest: AssetLoadTest }> {
  const payload = await request(`/api/v1/agent-asset-sources/${sourceId}/load-test`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!isRecord(payload)) throw new ApiError("加载测试结果格式无效", { code: "invalid_response" });
  return {
    source: isRecord(payload.source) ? parseSource(payload.source) : null,
    loadTest: parseLoadTest(loadTestDocument(payload)),
  };
}

export function hasRuntimePrerequisites(value: RuntimePrerequisites | null | undefined): boolean {
  return Boolean(value?.install_command || value?.init_command);
}

export function isLoadTestPassed(loadTest: AssetLoadTest | null | undefined): boolean {
  return loadTest?.status === "passed";
}

export async function scanAssetSource(sourceId: string, requestedRef?: string): Promise<AssetScan> {
  return parseScan(
    await request(`/api/v1/agent-asset-sources/${sourceId}/scan`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ requested_ref: requestedRef ?? null }),
    }),
  );
}

/**
 * Read the source's asset tree. Besides Git-scanned entries it may include the
 * assets the source's passed CLI load test generated; those entries carry
 * ``metadata.generated === true`` and are counted in ``counts``.
 */
export async function fetchAssetTree(sourceId: string, signal?: AbortSignal): Promise<AssetScan> {
  const payload = await request(`/api/v1/agent-asset-sources/${sourceId}/tree`, { signal });
  return parseScan(payload);
}

export async function resolveAgentAssets(body: {
  source_id: string;
  requested_ref?: string;
  paths: string[];
  types?: Record<string, AssetType>;
  runtime_key?: string;
}): Promise<{ created: boolean; snapshot: AssetSnapshot }> {
  const payload = await request("/api/v1/agent-assets/resolve", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!isRecord(payload) || !isRecord(payload.snapshot)) {
    throw new ApiError("资产快照解析结果无效", { code: "invalid_response" });
  }
  return { created: Boolean(payload.created), snapshot: parseSnapshot(payload.snapshot) };
}

export async function fetchAssetSnapshot(
  snapshotId: string,
  signal?: AbortSignal,
): Promise<AssetSnapshot> {
  return parseSnapshot(await request(`/api/v1/agent-asset-snapshots/${snapshotId}`, { signal }));
}

export async function fetchAssetSource(sourceId: string, signal?: AbortSignal): Promise<AssetSource> {
  return parseSource(await request(`/api/v1/agent-asset-sources/${sourceId}`, { signal }));
}

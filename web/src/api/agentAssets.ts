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
  runtime_prerequisites: Record<string, unknown>;
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

function parseAsset(raw: unknown): ScannedAsset {
  if (!isRecord(raw)) throw new ApiError("资产条目格式无效", { code: "invalid_response" });
  return {
    path: text(raw.path),
    asset_type: parseAssetType(raw.asset_type),
    name: text(raw.name, text(raw.path)),
    content_digest: text(raw.content_digest),
    metadata: isRecord(raw.metadata) ? (raw.metadata as AssetMetadata) : {},
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
    runtime_prerequisites: isRecord(raw.runtime_prerequisites) ? raw.runtime_prerequisites : {},
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

export async function scanAssetSource(sourceId: string, requestedRef?: string): Promise<AssetScan> {
  return parseScan(
    await request(`/api/v1/agent-asset-sources/${sourceId}/scan`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ requested_ref: requestedRef ?? null }),
    }),
  );
}

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

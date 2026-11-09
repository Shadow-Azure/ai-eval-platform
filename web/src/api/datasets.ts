import { ApiError } from "./types";
import { request } from "./evaluations";

export interface DatasetSummary {
  id: string;
  slug: string;
  name: string;
  adapter_key: string;
  task_family: string | null;
  environment_kind: string | null;
  created_at: string;
  version_count?: number;
}

export interface DatasetVersion {
  id: string;
  dataset_id: string;
  version: string;
  status: string;
  case_count: number;
  source_uri: string;
  source_revision: string;
  adapter_version: string;
  created_at: string;
}

export interface DatasetCaseSummary {
  id: string;
  dataset_version_id: string;
  case_key: string;
  ordinal: number;
  repo: string | null;
  base_commit: string | null;
  created_at: string;
}

export interface MetricDefinition {
  id: string;
  metric_key: string;
  version: number;
  display_name: string;
  description: string | null;
  aggregator_key: string;
  formula_text: string | null;
  display_kind: string;
  unit: string | null;
  direction: string;
  denominator_policy: string | null;
}

interface PageInfo {
  next_cursor: string | null;
  has_more: boolean;
  total?: number;
  limit?: number;
  offset?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function number(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function items(payload: unknown): { records: Record<string, unknown>[]; page: PageInfo } {
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的数据集数据", { code: "invalid_response" });
  }
  return {
    records: payload.items.filter(isRecord),
    page: isRecord(payload.page_info) ? (payload.page_info as unknown as PageInfo) : { next_cursor: null, has_more: false },
  };
}

function parseDataset(item: Record<string, unknown>): DatasetSummary {
  return {
    id: text(item.id),
    slug: text(item.slug),
    name: text(item.name),
    adapter_key: text(item.adapter_key),
    task_family: optionalText(item.task_family),
    environment_kind: optionalText(item.environment_kind),
    created_at: text(item.created_at),
    version_count: typeof item.version_count === "number" ? item.version_count : undefined,
  };
}

function parseVersion(item: Record<string, unknown>): DatasetVersion {
  return {
    id: text(item.id),
    dataset_id: text(item.dataset_id),
    version: text(item.version),
    status: text(item.status),
    case_count: number(item.case_count),
    source_uri: text(item.source_uri),
    source_revision: text(item.source_revision),
    adapter_version: text(item.adapter_version),
    created_at: text(item.created_at),
  };
}

function parseCase(item: Record<string, unknown>): DatasetCaseSummary {
  return {
    id: text(item.id),
    dataset_version_id: text(item.dataset_version_id),
    case_key: text(item.case_key),
    ordinal: number(item.ordinal),
    repo: optionalText(item.repo),
    base_commit: optionalText(item.base_commit),
    created_at: text(item.created_at),
  };
}

function parseMetric(item: Record<string, unknown>): MetricDefinition {
  return {
    id: text(item.id),
    metric_key: text(item.metric_key),
    version: number(item.version),
    display_name: text(item.display_name),
    description: optionalText(item.description),
    aggregator_key: text(item.aggregator_key),
    formula_text: optionalText(item.formula_text),
    display_kind: text(item.display_kind),
    unit: optionalText(item.unit),
    direction: text(item.direction),
    denominator_policy: optionalText(item.denominator_policy),
  };
}

export async function fetchDatasets(signal?: AbortSignal): Promise<DatasetSummary[]> {
  const payload = await request("/api/v1/datasets?limit=50", { signal });
  return items(payload).records.map(parseDataset);
}

export async function fetchDataset(id: string, signal?: AbortSignal): Promise<DatasetSummary> {
  const payload = await request(`/api/v1/datasets/${id}`, { signal });
  if (!isRecord(payload)) throw new ApiError("数据集响应无效", { code: "invalid_response" });
  return parseDataset(payload);
}

export async function fetchDatasetVersions(
  datasetId: string,
  signal?: AbortSignal,
): Promise<DatasetVersion[]> {
  const payload = await request(`/api/v1/datasets/${datasetId}/versions?limit=50`, { signal });
  return items(payload).records.map(parseVersion);
}

export async function fetchDatasetVersion(
  versionId: string,
  signal?: AbortSignal,
): Promise<DatasetVersion & { metrics: MetricDefinition[] }> {
  const payload = await request(`/api/v1/dataset-versions/${versionId}`, { signal });
  if (!isRecord(payload)) throw new ApiError("数据集版本响应无效", { code: "invalid_response" });
  return {
    ...parseVersion(payload),
    metrics: Array.isArray(payload.metrics) ? payload.metrics.filter(isRecord).map(parseMetric) : [],
  };
}

export async function fetchDatasetCases(
  versionId: string,
  signal?: AbortSignal,
): Promise<DatasetCaseSummary[]> {
  const payload = await request(
    `/api/v1/dataset-versions/${versionId}/cases?limit=200`,
    { signal },
  );
  return items(payload).records.map(parseCase);
}

export interface ImportResult {
  created: boolean;
  status: string;
  import: { id: string; status: string; valid_count: number; invalid_count: number };
  dataset: DatasetSummary | null;
  version: DatasetVersion | null;
}

export async function createDatasetImport(body: {
  dataset_slug: string;
  dataset_name: string;
  adapter_key: string;
  version: string;
  source_uri?: string;
  source_revision: string;
  records: Record<string, unknown>[];
}): Promise<ImportResult> {
  const payload = await request("/api/v1/dataset-imports", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!isRecord(payload)) throw new ApiError("导入响应无效", { code: "invalid_response" });
  return {
    created: Boolean(payload.created),
    status: text(payload.status),
    import: isRecord(payload.import)
      ? {
          id: text(payload.import.id),
          status: text(payload.import.status),
          valid_count: number(payload.import.valid_count),
          invalid_count: number(payload.import.invalid_count),
        }
      : { id: "", status: "", valid_count: 0, invalid_count: 0 },
    dataset: isRecord(payload.dataset) ? parseDataset(payload.dataset) : null,
    version: isRecord(payload.version) ? parseVersion(payload.version) : null,
  };
}

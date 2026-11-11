import { ApiError, notifyUnauthorized } from "./types";
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

export interface DatasetLatestVersion {
  id: string;
  version: string;
  status: string;
  case_count: number;
  created_at: string;
}

export interface DatasetPrimaryMetric {
  metric_key: string;
  version: number;
  display_name: string;
  display_kind: string;
  unit: string | null;
  direction: string;
}

export interface DatasetAdapterRef {
  key: string;
  display_name: string;
  registered: boolean;
}

/** M5-13 目录卡片：数据集行 + 最新版本 + 主指标 + 适配器元数据。 */
export interface DatasetCatalogEntry extends DatasetSummary {
  description: string;
  updated_at: string | null;
  latest_version: DatasetLatestVersion | null;
  primary_metric: DatasetPrimaryMetric | null;
  adapter: DatasetAdapterRef;
  status: string;
}

export interface DatasetAdapterMetadata {
  key: string;
  adapter_version: string;
  display_name: string;
  task_family: string | null;
  environment_kind: string | null;
  description: string;
  primary_metric_key: string | null;
  metric_keys: string[];
  metric_display_names: Record<string, string>;
  case_visibility: Record<string, string>;
  dataset_count: number;
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
  execution_image_digest: string | null;
  scoring_image_digest: string | null;
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
  next_offset?: number | null;
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
    execution_image_digest: optionalText(item.execution_image_digest),
    scoring_image_digest: optionalText(item.scoring_image_digest),
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

function parseLatestVersion(item: unknown): DatasetLatestVersion | null {
  if (!isRecord(item)) return null;
  return {
    id: text(item.id),
    version: text(item.version),
    status: text(item.status),
    case_count: number(item.case_count),
    created_at: text(item.created_at),
  };
}

function parsePrimaryMetric(item: unknown): DatasetPrimaryMetric | null {
  if (!isRecord(item)) return null;
  return {
    metric_key: text(item.metric_key),
    version: number(item.version),
    display_name: text(item.display_name),
    display_kind: text(item.display_kind),
    unit: optionalText(item.unit),
    direction: text(item.direction),
  };
}

function parseCatalogEntry(item: Record<string, unknown>): DatasetCatalogEntry {
  const base = parseDataset(item);
  const adapter = isRecord(item.adapter)
    ? {
        key: text(item.adapter.key),
        display_name: text(item.adapter.display_name),
        registered: Boolean(item.adapter.registered),
      }
    : { key: base.adapter_key, display_name: base.adapter_key, registered: false };
  return {
    ...base,
    description: text(item.description),
    updated_at: optionalText(item.updated_at),
    latest_version: parseLatestVersion(item.latest_version),
    primary_metric: parsePrimaryMetric(item.primary_metric),
    adapter,
    status: text(item.status) || (item.latest_version ? "ready" : "draft"),
  };
}

function parseAdapterMetadata(item: Record<string, unknown>): DatasetAdapterMetadata {
  const names = isRecord(item.metric_display_names) ? item.metric_display_names : {};
  const visibility = isRecord(item.case_visibility) ? item.case_visibility : {};
  return {
    key: text(item.key),
    adapter_version: text(item.adapter_version),
    display_name: text(item.display_name),
    task_family: optionalText(item.task_family),
    environment_kind: optionalText(item.environment_kind),
    description: text(item.description),
    primary_metric_key: optionalText(item.primary_metric_key),
    metric_keys: Array.isArray(item.metric_keys)
      ? item.metric_keys.filter((value): value is string => typeof value === "string")
      : [],
    metric_display_names: Object.fromEntries(
      Object.entries(names).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
    ),
    case_visibility: Object.fromEntries(
      Object.entries(visibility).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
    ),
    dataset_count: number(item.dataset_count),
  };
}

/** 拉取全部目录条目：按 200/页翻页直到没有下一页，避免 >200 时统计与筛选静默缺数据。 */
export async function fetchDatasetCatalog(signal?: AbortSignal): Promise<DatasetCatalogEntry[]> {
  const entries: DatasetCatalogEntry[] = [];
  let offset = 0;
  // 上限只防止后端异常时无限循环；正常目录远小于 50 页。
  for (let page = 0; page < 50; page += 1) {
    const payload = await request(`/api/v1/datasets?limit=200&offset=${offset}`, { signal });
    const { records, page: info } = items(payload);
    entries.push(...records.map(parseCatalogEntry));
    if (!info.has_more || records.length === 0) break;
    offset = info.next_offset ?? offset + records.length;
  }
  return entries;
}

export async function fetchDatasetAdapters(signal?: AbortSignal): Promise<DatasetAdapterMetadata[]> {
  const payload = await request("/api/v1/dataset-adapters", { signal });
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的适配器注册表", { code: "invalid_response" });
  }
  return payload.items.filter(isRecord).map(parseAdapterMetadata);
}

export async function fetchDatasetImports(
  limit = 3,
  signal?: AbortSignal,
): Promise<DatasetImportRecord[]> {
  const payload = await request(`/api/v1/dataset-imports?limit=${limit}`, { signal });
  return items(payload).records.map(parseImportRecord);
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

export interface DatasetCasePage {
  items: DatasetCaseSummary[];
  total: number;
}

/** Server-paginated case list used by the launch wizard's subset picker. */
export async function fetchDatasetCasesPage(
  versionId: string,
  params: { limit?: number; offset?: number },
  signal?: AbortSignal,
): Promise<DatasetCasePage> {
  const limit = params.limit ?? 20;
  const offset = params.offset ?? 0;
  const payload = await request(
    `/api/v1/dataset-versions/${versionId}/cases?limit=${limit}&offset=${offset}`,
    { signal },
  );
  const { records, page } = items(payload);
  return { items: records.map(parseCase), total: page.total ?? records.length };
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
  execution_image_digest?: string;
  scoring_image_digest?: string;
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

// ---------------------------------------------------------------------------
// M5-15 uploaded imports
// ---------------------------------------------------------------------------

export interface ImportIssue {
  code: string;
  field: string | null;
  message: string;
  case_index: number | null;
  case_key: string | null;
}

export interface FieldCompleteness {
  present: number;
  percent: number;
}

export interface ImportReport {
  mode?: string;
  total?: number;
  valid?: number;
  invalid?: number;
  field_completeness?: Record<string, FieldCompleteness>;
  error_codes?: Record<string, number>;
  error_fields?: Record<string, number>;
  errors?: ImportIssue[];
  errors_truncated?: number;
  warnings?: string[];
  fatal?: boolean;
  fatal_code?: string;
  fatal_message?: string;
  generated_at?: string;
}

export interface DatasetImportRecord {
  id: string;
  dataset_id: string | null;
  dataset_slug: string;
  adapter_key: string;
  version: string;
  source_uri: string;
  source_revision: string | null;
  status: string;
  total_count: number;
  valid_count: number;
  invalid_count: number;
  processed_count: number;
  file_name: string | null;
  file_size: number | null;
  file_digest: string | null;
  report: ImportReport;
  error_report: ImportReport;
  created_by: string;
  created_at: string;
  status_updated_at: string | null;
  dataset?: DatasetSummary | null;
  version_detail?: DatasetVersion | null;
}

function parseReport(value: unknown): ImportReport {
  return isRecord(value) ? (value as unknown as ImportReport) : {};
}

function parseImportRecord(payload: unknown): DatasetImportRecord {
  if (!isRecord(payload)) {
    throw new ApiError("控制面返回了无法识别的导入数据", { code: "invalid_response" });
  }
  return {
    id: text(payload.id),
    dataset_id: optionalText(payload.dataset_id),
    dataset_slug: text(payload.dataset_slug),
    adapter_key: text(payload.adapter_key),
    version: text(payload.version),
    source_uri: text(payload.source_uri),
    source_revision: optionalText(payload.source_revision),
    status: text(payload.status),
    total_count: number(payload.total_count),
    valid_count: number(payload.valid_count),
    invalid_count: number(payload.invalid_count),
    processed_count: number(payload.processed_count),
    file_name: optionalText(payload.file_name),
    file_size: typeof payload.file_size === "number" ? payload.file_size : null,
    file_digest: optionalText(payload.file_digest),
    report: parseReport(payload.report),
    error_report: parseReport(payload.error_report),
    created_by: text(payload.created_by),
    created_at: text(payload.created_at),
    status_updated_at: optionalText(payload.status_updated_at),
    dataset: isRecord(payload.dataset) ? parseDataset(payload.dataset) : null,
    version_detail: isRecord(payload.version_detail) ? parseVersion(payload.version_detail) : null,
  };
}

export async function fetchDatasetImport(
  importId: string,
  signal?: AbortSignal,
): Promise<DatasetImportRecord> {
  const payload = await request(`/api/v1/dataset-imports/${importId}`, { signal });
  return parseImportRecord(payload);
}

function apiErrorFromBody(status: number, payload: unknown): ApiError {
  const detail = isRecord(payload) && isRecord(payload.detail) ? payload.detail : null;
  const error = detail && isRecord(detail.error) ? detail.error : null;
  return new ApiError(
    error && typeof error.message === "string" ? error.message : `导入请求失败（HTTP ${status}）`,
    {
      status,
      code: error && typeof error.code === "string" ? error.code : null,
      details:
        error && isRecord(error.details) ? (error.details as Record<string, unknown>) : null,
    },
  );
}

export interface UploadImportMeta {
  dataset_slug: string;
  dataset_name: string;
  adapter_key: string;
  version: string;
  source_revision: string;
  execution_image_digest?: string;
  scoring_image_digest?: string;
}

export function uploadDatasetImport(
  meta: UploadImportMeta,
  file: File,
  options: { onProgress?: (ratio: number) => void; signal?: AbortSignal } = {},
): Promise<DatasetImportRecord> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const [key, value] of Object.entries(meta)) {
      if (typeof value === "string" && value.trim()) {
        form.append(key, value);
      }
    }
    form.append("file", file);

    const xhr = new XMLHttpRequest();
    let aborted = false;
    xhr.open("POST", "/api/v1/dataset-imports/uploads");
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        options.onProgress?.(Math.min(1, event.loaded / event.total));
      }
    };
    xhr.onerror = () => {
      reject(new ApiError("无法连接控制面 API，请确认后端服务已启动。", { code: "network_error" }));
    };
    xhr.onabort = () => {
      aborted = true;
      reject(new ApiError("已取消上传", { code: "aborted" }));
    };
    xhr.onload = () => {
      let payload: unknown = null;
      try {
        payload = JSON.parse(xhr.responseText) as unknown;
      } catch {
        payload = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && isRecord(payload) && isRecord(payload.import)) {
        resolve(parseImportRecord(payload.import));
        return;
      }
      notifyUnauthorized(xhr.status);
      reject(apiErrorFromBody(xhr.status, payload));
    };
    options.signal?.addEventListener("abort", () => {
      if (!aborted) xhr.abort();
    });
    xhr.send(form);
  });
}

export async function commitDatasetImport(importId: string): Promise<DatasetImportRecord> {
  const payload = await request(`/api/v1/dataset-imports/${importId}/commit`, { method: "POST" });
  if (!isRecord(payload) || !isRecord(payload.import)) {
    throw new ApiError("提交响应无效", { code: "invalid_response" });
  }
  return parseImportRecord(payload.import);
}

export async function cancelDatasetImport(importId: string): Promise<DatasetImportRecord> {
  const payload = await request(`/api/v1/dataset-imports/${importId}/cancel`, { method: "POST" });
  return parseImportRecord(payload);
}

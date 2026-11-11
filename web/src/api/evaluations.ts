import { ApiError, notifyUnauthorized } from "./types";

export type EvaluationStatus =
  | "preparing"
  | "queued"
  | "running"
  | "finalizing"
  | "completed"
  | "partial"
  | "failed"
  | "pausing_budget"
  | "paused_budget"
  | "cancelling"
  | "cancelled";

export const TERMINAL_EVAL_STATUSES: readonly string[] = [
  "completed",
  "partial",
  "failed",
  "cancelled",
];

export interface EvaluationSummary {
  id: string;
  name: string;
  status: string;
  total_cases: number;
  queued_count: number;
  running_count: number;
  succeeded_count: number;
  failed_count: number;
  finished_count?: number;
  unfinished_count?: number;
  shard_size: number;
  requested_concurrency?: number;
  effective_concurrency?: number | null;
  queue_reason?: string | null;
  resource_pool_id?: string | null;
  case_timeout_seconds?: number;
  max_attempts?: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  idempotency_key: string;
  created_by: string;
  dataset_version_id?: string | null;
  dataset_name?: string | null;
  dataset_version?: string | null;
  resolved_count?: number | null;
  scored_count?: number | null;
  resolve_rate?: number | null;
  duration_seconds?: number | null;
  runtime_key?: string | null;
  runtime_version?: string | null;
  model_name?: string | null;
  model_budget_cny?: string | null;
  budget_spent_cny?: string | null;
  budget_remaining_cny?: string | null;
  budget_version?: number;
  pause_reason?: string | null;
  execution_image_digest?: string | null;
  asset_snapshot_id?: string | null;
  asset_resolved_commit?: string | null;
  asset_count?: number | null;
}

export interface FrozenAsset {
  asset_type: string;
  name: string;
  path: string;
  content_digest?: string;
  metadata?: Record<string, unknown>;
}

export interface FrozenAssetSnapshot {
  snapshot_id?: string | null;
  source_id?: string | null;
  requested_ref?: string | null;
  resolved_commit?: string | null;
  tree_digest?: string | null;
  bundle_digest?: string | null;
  bundle_size?: number | null;
  file_count?: number | null;
  asset_count?: number | null;
  runtime_key?: string | null;
  assets?: FrozenAsset[];
}

export interface CaseTestSummary {
  passed: number;
  failed: number;
  skipped: number;
  error: number;
  f2p_failed: number;
  p2p_failed: number;
}

export interface EvaluationCase {
  id: string;
  case_key: string;
  ordinal: number;
  repo?: string | null;
  execution_status: string;
  result_status: string;
  trace_status?: string;
  latest_attempt_no: number;
  duration_ms: number | null;
  error_code: string | null;
  error_summary: string | null;
  metrics?: Record<string, unknown>;
  test_summary?: CaseTestSummary;
  updated_at: string | null;
}

export interface EvaluationShard {
  shard_id: string | null;
  dispatch_key: string;
  attempt: number;
  status: string;
  manifest_digest: string | null;
  dispatched_at: string | null;
  github_status: number | null;
  attempts: number;
  last_error: string | null;
}

export interface EvaluationProgress {
  total: number;
  finished: number;
  percent: number;
  resolve_rate: number | null;
  estimated_remaining_seconds: number | null;
  estimated_finish_at: string | null;
}

export interface EvaluationDetail extends EvaluationSummary {
  cases: EvaluationCase[];
  shards: EvaluationShard[];
  /** Server-side aggregate snapshot; the page must not recompute these. */
  progress: EvaluationProgress;
  asset_snapshot?: FrozenAssetSnapshot | null;
  consistency?: Record<string, unknown>;
  case_counts?: Record<string, number>;
  trace_counts?: Record<string, number>;
  retryable_count?: number;
}

export interface EvaluationEvent {
  id: number;
  event_type: string;
  case_run_id: string | null;
  attempt_no: number | null;
  sequence: number;
  payload: Record<string, unknown>;
  occurred_at: string | null;
}

export interface EvaluationListStats {
  total: number;
  active: number;
  running_cases: number;
  resolved_cases: number;
  scored_cases: number;
  avg_resolve_rate: number | null;
  avg_duration_seconds: number | null;
}

export interface EvaluationListQuery {
  q?: string;
  status?: string;
  datasetId?: string;
  limit?: number;
  offset?: number;
}

export interface EvaluationListResponse {
  status: string;
  total: number;
  items: EvaluationSummary[];
  stats: EvaluationListStats;
}

export interface CreateEvaluationRequest {
  name?: string;
  case_keys?: string[];
  shard_size?: number;
  idempotency_key?: string;
  dataset_version_id?: string;
  model_config_id?: string;
  model_name?: string;
  runtime_key?: string;
  runtime_version?: string;
  resource_pool_id?: string;
  scoring_image_digest?: string;
  execution_image_digest?: string;
  assets_resolved?: boolean;
  asset_snapshot_id?: string;
  asset_source_id?: string;
  requested_ref?: string;
  asset_paths?: string[];
  asset_types?: Record<string, string>;
  preparation_id?: string;
  case_timeout_seconds?: number;
  max_attempts?: number;
}

export interface PreflightIssue {
  code: string;
  message: string;
  field?: string;
  details?: Record<string, unknown>;
}

export type CaseSelectionMode = "all" | "count" | "cases";

export interface CaseSelection {
  mode: CaseSelectionMode;
  count?: number | null;
  case_keys?: string[] | null;
}

export interface ExecutionPolicy {
  concurrency?: number | null;
  case_timeout_seconds?: number | null;
  max_attempts?: number | null;
  model_budget_cny?: number | null;
  shard_size?: number | null;
}

export interface ResolvedExecutionPolicy {
  concurrency: number;
  case_timeout_seconds: number;
  max_attempts: number;
  model_budget_cny: number | null;
  shard_size: number;
  execution_image_digest: string | null;
  scoring_image_digest: string | null;
}

export interface CostRange {
  low: string;
  high: string;
}

export interface CostEstimate {
  currency: string;
  model_cny: CostRange;
  runner_cny: CostRange | null;
  total_cny: CostRange;
  estimated_duration_seconds: { low: number; high: number };
  basis: string;
  assumptions: Record<string, number>;
}

export interface PreparationDataset {
  id: string;
  name: string;
  version: string;
  adapter_key: string;
  case_count: number;
  source_revision: string;
  execution_image_digest: string | null;
  scoring_image_digest: string | null;
}

export interface PreparationAsset {
  id: string;
  source_id?: string | null;
  requested_ref?: string | null;
  resolved_commit?: string | null;
  asset_count?: number | null;
}

export interface PreparationSummary {
  dataset: PreparationDataset;
  case_count: number;
  estimated_job_count: number;
  runtime: { key: string; version: string | null };
  asset_snapshot: PreparationAsset | null;
  model: {
    config_id: string;
    display_name: string | null;
    provider: string | null;
    model_name: string | null;
  };
  resource_pool: {
    id: string;
    name: string | null;
    provider_key: string | null;
    max_capacity: number | null;
    online_runners: number;
    price_cny: string | null;
  };
  execution: ResolvedExecutionPolicy;
  resolved: {
    effective_concurrency: number;
    execution_image_digest: string | null;
    scoring_image_digest: string | null;
    estimated_job_count: number;
  };
  cost_estimate: CostEstimate | null;
  warnings: PreflightIssue[];
}

export interface PrepareResponse {
  ready: boolean;
  blockers: PreflightIssue[];
  warnings: PreflightIssue[];
  asset_snapshot?: FrozenAssetSnapshot | null;
  preparation_id: string | null;
  expires_at: string | null;
  snapshot_digest: string | null;
  case_count: number;
  estimated_job_count: number;
  resolved_commit: string | null;
  effective_concurrency: number;
  execution_image_digest: string | null;
  scoring_image_digest: string | null;
  case_selection: CaseSelection | null;
  execution: ResolvedExecutionPolicy | null;
  cost_estimate: CostEstimate | null;
  summary: PreparationSummary | null;
}

export interface PrepareRequest {
  dataset_version_id: string;
  model_config_id: string;
  model_name?: string;
  runtime_key?: string;
  runtime_version?: string;
  resource_pool_id: string;
  requested_concurrency?: number;
  scoring_image_digest?: string;
  execution_image_digest?: string;
  assets_resolved?: boolean;
  asset_snapshot_id?: string;
  case_selection?: CaseSelection;
  execution?: ExecutionPolicy;
  model_budget_cny?: number;
  shard_size?: number;
}

export interface MetricResult {
  metric_key: string;
  metric_version: number;
  display_name: string;
  description: string | null;
  formula_text: string | null;
  display_kind: string;
  unit: string | null;
  direction: string;
  denominator_policy: string | null;
  value: unknown;
  numerator: string | null;
  denominator: string | null;
  is_final: boolean;
  generated_at: string | null;
}

export interface MetricDelta {
  metric_key: string;
  metric_version: number | null;
  baseline_metric_version: number | null;
  display_name: string;
  description: string | null;
  formula_text: string | null;
  display_kind: string;
  unit: string | null;
  direction: string;
  denominator_policy: string | null;
  current_value: unknown;
  baseline_value: unknown;
  current_numerator: string | null;
  current_denominator: string | null;
  baseline_numerator: string | null;
  baseline_denominator: string | null;
  delta: number | Record<string, number> | null;
  delta_ratio: number | Record<string, number> | null;
  improvement: boolean | Record<string, boolean | null> | null;
  comparable: boolean;
  incomparable_reason: string | null;
  current_is_final: boolean;
  baseline_is_final: boolean;
}

export interface ConfigurationDifference {
  key: string;
  label: string;
  category: string;
  current: string;
  baseline: string;
  changed: boolean;
  incomparable: boolean;
  note: string | null;
}

export interface BaselineCandidate {
  id: string;
  name: string;
  status: string;
  created_at: string | null;
  finished_at: string | null;
  dataset_version_id: string | null;
  dataset_label: string;
  runtime_key: string | null;
  runtime_version: string | null;
  model_name: string;
  asset_resolved_commit: string | null;
  case_count: number;
  comparable: boolean;
  reasons: string[];
}

export interface BaselineSummary {
  id: string;
  name: string;
  status: string;
  created_at: string | null;
  finished_at: string | null;
  dataset_version_id: string | null;
  dataset_label: string;
  runtime_key: string | null;
  runtime_version: string | null;
  model_name: string;
  asset_resolved_commit: string | null;
  resource_pool_id: string | null;
  case_count: number;
  case_digest: string;
}

export interface BaselineComparison {
  run_id: string;
  run_name: string;
  status: string;
  is_final: boolean;
  manual: boolean;
  baseline_run_id: string | null;
  recommendation: { tier: string; reason: string } | null;
  baseline: BaselineSummary | null;
  candidates: BaselineCandidate[];
  comparable: boolean;
  incomparable_reasons: string[];
  case_set: {
    comparable: boolean;
    current_count: number;
    baseline_count: number;
  };
  metric_deltas: MetricDelta[];
  configuration_differences: ConfigurationDifference[];
  empty_state: { code: string; message: string } | null;
  generated_at: string | null;
}

export interface CaseTestResult {
  id: string;
  suite_name: string;
  test_name: string;
  outcome: string;
  native_status: string;
  test_group: string;
  expected: boolean;
  duration_ms: number | null;
  file_path: string | null;
  line_no: number | null;
  failure_message: string | null;
  log_object_key: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorObject(payload: unknown): unknown {
  if (!isRecord(payload)) return null;
  if ("error" in payload) return payload.error;
  if ("detail" in payload && isRecord(payload.detail) && "error" in payload.detail) {
    return (payload.detail as { error?: unknown }).error;
  }
  return null;
}

function errorInfo(payload: unknown): {
  code: string | null;
  message: string | null;
  fields: Record<string, string> | null;
  details: Record<string, unknown> | null;
} {
  const error = errorObject(payload);
  if (!isRecord(error)) {
    return { code: null, message: null, fields: null, details: null };
  }
  const fields = isRecord(error.fields)
    ? Object.fromEntries(
        Object.entries(error.fields).filter(
          (entry): entry is [string, string] => typeof entry[1] === "string",
        ),
      )
    : null;
  return {
    code: typeof error.code === "string" ? error.code : null,
    message: typeof error.message === "string" ? error.message : null,
    fields: fields && Object.keys(fields).length > 0 ? fields : null,
    details: isRecord(error.details) ? error.details : null,
  };
}

function errorFields(payload: unknown): Record<string, string> {
  const error = errorObject(payload);
  if (!isRecord(error)) return {};
  let raw: unknown = error.fields;
  if (!isRecord(raw) && isRecord(error.details) && isRecord(error.details.fields)) {
    raw = error.details.fields;
  }
  if (!isRecord(raw)) return {};
  const fields: Record<string, string> = {};
  for (const [name, value] of Object.entries(raw)) {
    if (typeof value === "string" && value.trim()) fields[name] = value;
  }
  return fields;
}

export async function request(path: string, init?: RequestInit): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
      credentials: "same-origin",
    });
  } catch (error) {
    throw new ApiError("无法连接控制面 API，请确认后端服务已启动。", {
      code: "network_error",
      cause: error,
    });
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    notifyUnauthorized(response.status);
    const info = errorInfo(payload);
    throw new ApiError(info.message ?? `控制面请求失败（HTTP ${response.status}）`, {
      status: response.status,
      code: info.code,
      fields: errorFields(payload),
      details: info.details,
    });
  }
  return payload;
}

function readCount(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new ApiError(`控制面响应中的 ${field} 无效`, { code: "invalid_response" });
  }
  return value;
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readOptionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function readOptionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readStats(payload: unknown): EvaluationListStats {
  if (!isRecord(payload)) {
    return {
      total: 0,
      active: 0,
      running_cases: 0,
      resolved_cases: 0,
      scored_cases: 0,
      avg_resolve_rate: null,
      avg_duration_seconds: null,
    };
  }
  return {
    total: readCountOrZero(payload.total),
    active: readCountOrZero(payload.active),
    running_cases: readCountOrZero(payload.running_cases),
    resolved_cases: readCountOrZero(payload.resolved_cases),
    scored_cases: readCountOrZero(payload.scored_cases),
    avg_resolve_rate: readOptionalNumber(payload.avg_resolve_rate),
    avg_duration_seconds: readOptionalNumber(payload.avg_duration_seconds),
  };
}

export function parseEvaluationSummary(payload: unknown): EvaluationSummary {
  if (!isRecord(payload)) {
    throw new ApiError("控制面返回了无法识别的评测数据", { code: "invalid_response" });
  }
  return {
    id: readString(payload.id),
    name: readString(payload.name),
    status: readString(payload.status),
    total_cases: readCount(payload.total_cases, "total_cases"),
    queued_count: readCount(payload.queued_count, "queued_count"),
    running_count: readCount(payload.running_count, "running_count"),
    succeeded_count: readCount(payload.succeeded_count, "succeeded_count"),
    failed_count: readCount(payload.failed_count, "failed_count"),
    shard_size: readCount(payload.shard_size, "shard_size"),
    requested_concurrency:
      typeof payload.requested_concurrency === "number" ? payload.requested_concurrency : undefined,
    effective_concurrency:
      typeof payload.effective_concurrency === "number" ? payload.effective_concurrency : null,
    queue_reason: readOptionalString(payload.queue_reason),
    resource_pool_id: readOptionalString(payload.resource_pool_id),
    case_timeout_seconds:
      typeof payload.case_timeout_seconds === "number" ? payload.case_timeout_seconds : undefined,
    max_attempts: typeof payload.max_attempts === "number" ? payload.max_attempts : undefined,
    created_at: readOptionalString(payload.created_at),
    started_at: readOptionalString(payload.started_at),
    finished_at: readOptionalString(payload.finished_at),
    idempotency_key: readString(payload.idempotency_key),
    created_by: readString(payload.created_by),
    dataset_version_id: readOptionalString(payload.dataset_version_id),
    dataset_name: readOptionalString(payload.dataset_name),
    dataset_version: readOptionalString(payload.dataset_version),
    resolved_count: readOptionalNumber(payload.resolved_count),
    scored_count: readOptionalNumber(payload.scored_count),
    resolve_rate: readOptionalNumber(payload.resolve_rate),
    duration_seconds: readOptionalNumber(payload.duration_seconds),
    runtime_key: readOptionalString(payload.runtime_key),
    runtime_version: readOptionalString(payload.runtime_version),
    model_name: readOptionalString(payload.model_name),
    model_budget_cny: readOptionalString(payload.model_budget_cny),
    budget_spent_cny: readOptionalString(payload.budget_spent_cny),
    budget_remaining_cny: readOptionalString(payload.budget_remaining_cny),
    budget_version: typeof payload.budget_version === "number" ? payload.budget_version : 1,
    pause_reason: readOptionalString(payload.pause_reason),
    finished_count: typeof payload.finished_count === "number" ? payload.finished_count : undefined,
    unfinished_count: typeof payload.unfinished_count === "number" ? payload.unfinished_count : undefined,
    execution_image_digest: readOptionalString(payload.execution_image_digest),
    asset_snapshot_id: readOptionalString(payload.asset_snapshot_id),
    asset_resolved_commit: readOptionalString(payload.asset_resolved_commit),
    asset_count: typeof payload.asset_count === "number" ? payload.asset_count : undefined,
  };
}

export function parseEvaluationList(payload: unknown): EvaluationListResponse {
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的评测列表", { code: "invalid_response" });
  }
  return {
    status: readString(payload.status),
    total: readCount(payload.total, "total"),
    items: payload.items.map(parseEvaluationSummary),
    stats: readStats(payload.stats),
  };
}

export function parseEvaluationDetail(payload: unknown): EvaluationDetail {
  const summary = parseEvaluationSummary(payload);
  const record = payload as Record<string, unknown>;
  const cases = Array.isArray(record.cases) ? record.cases : [];
  const shards = Array.isArray(record.shards) ? record.shards : [];
  return {
    ...summary,
    asset_snapshot: isRecord(record.asset_snapshot)
      ? (record.asset_snapshot as unknown as FrozenAssetSnapshot)
      : undefined,
    consistency: isRecord(record.consistency) ? record.consistency : undefined,
    case_counts: readNumberMap(record.case_counts),
    trace_counts: readNumberMap(record.trace_counts),
    progress: readProgress(record.progress),
    retryable_count:
      typeof record.retryable_count === "number" ? record.retryable_count : undefined,
    cases: cases.map((entry) => {
      const item = (entry ?? {}) as Record<string, unknown>;
      return {
        id: readString(item.id),
        case_key: readString(item.case_key),
        ordinal: readCount(item.ordinal, "ordinal"),
        repo: readOptionalString(item.repo),
        execution_status: readString(item.execution_status),
        result_status: readString(item.result_status),
        trace_status: readOptionalString(item.trace_status) ?? "pending",
        latest_attempt_no: readCount(item.latest_attempt_no, "latest_attempt_no"),
        duration_ms: typeof item.duration_ms === "number" ? item.duration_ms : null,
        error_code: readOptionalString(item.error_code),
        error_summary: readOptionalString(item.error_summary),
        updated_at: readOptionalString(item.updated_at),
      };
    }),
    shards: shards.map((entry) => {
      const item = (entry ?? {}) as Record<string, unknown>;
      return {
        shard_id: readOptionalString(item.shard_id),
        dispatch_key: readString(item.dispatch_key),
        attempt: readCount(item.attempt, "attempt"),
        status: readString(item.status),
        manifest_digest: readOptionalString(item.manifest_digest),
        dispatched_at: readOptionalString(item.dispatched_at),
        github_status: typeof item.github_status === "number" ? item.github_status : null,
        attempts: readCount(item.attempts, "attempts"),
        last_error: readOptionalString(item.last_error),
      };
    }),
  };
}

function readProgress(value: unknown): EvaluationProgress {
  if (!isRecord(value)) {
    throw new ApiError("控制面响应缺少 progress 聚合字段", { code: "invalid_response" });
  }
  return {
    total: readCount(value.total, "progress.total"),
    finished: readCount(value.finished, "progress.finished"),
    percent: readCount(value.percent, "progress.percent"),
    resolve_rate: typeof value.resolve_rate === "number" ? value.resolve_rate : null,
    estimated_remaining_seconds:
      typeof value.estimated_remaining_seconds === "number"
        ? value.estimated_remaining_seconds
        : null,
    estimated_finish_at: readOptionalString(value.estimated_finish_at),
  };
}

function readNumberMap(value: unknown): Record<string, number> | undefined {
  if (!isRecord(value)) return undefined;
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [string, number] => typeof entry[1] === "number",
    ),
  );
}

export async function fetchEvaluations(
  query: EvaluationListQuery = {},
  signal?: AbortSignal,
): Promise<EvaluationListResponse> {
  const params = new URLSearchParams({ limit: String(query.limit ?? 50) });
  if (query.offset) params.set("offset", String(query.offset));
  if (query.q?.trim()) params.set("q", query.q.trim());
  if (query.status) params.set("status", query.status);
  if (query.datasetId) params.set("dataset_id", query.datasetId);
  return parseEvaluationList(await request(`/api/v1/evaluations?${params.toString()}`, { signal }));
}

export async function fetchEvaluationDetail(
  runId: string,
  signal?: AbortSignal,
): Promise<EvaluationDetail> {
  return parseEvaluationDetail(await request(`/api/v1/evaluations/${runId}`, { signal }));
}

export interface EvaluationEventPage {
  items: EvaluationEvent[];
  next_cursor: number | null;
  has_more: boolean;
  direction: "older" | "newer";
}

export interface EvaluationEventQuery {
  afterId?: number;
  beforeId?: number;
  limit?: number;
}

export async function fetchEvaluationEvents(
  runId: string,
  query: EvaluationEventQuery = {},
  signal?: AbortSignal,
): Promise<EvaluationEventPage> {
  const params = new URLSearchParams();
  params.set("limit", String(query.limit ?? 100));
  if (query.afterId !== undefined) params.set("after_id", String(query.afterId));
  if (query.beforeId !== undefined) params.set("before_id", String(query.beforeId));
  const payload = await request(`/api/v1/evaluations/${runId}/events?${params.toString()}`, {
    signal,
  });
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的事件列表", { code: "invalid_response" });
  }
  return {
    items: payload.items.map((entry) => {
      const item = (entry ?? {}) as Record<string, unknown>;
      return {
        id: readCount(item.id, "id"),
        event_type: readString(item.event_type),
        case_run_id: readOptionalString(item.case_run_id),
        attempt_no: typeof item.attempt_no === "number" ? item.attempt_no : null,
        sequence: readCount(item.sequence, "sequence"),
        payload: isRecord(item.payload) ? item.payload : {},
        occurred_at: readOptionalString(item.occurred_at),
      };
    }),
    next_cursor: typeof payload.next_cursor === "number" ? payload.next_cursor : null,
    has_more: Boolean(payload.has_more),
    direction: payload.direction === "newer" ? "newer" : "older",
  };
}

export async function createEvaluation(
  body: CreateEvaluationRequest,
  idempotencyKey?: string,
): Promise<EvaluationSummary> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  // The preparation contract requires the key to ride on the header; a body
  // copy is kept for older synthetic-only callers.
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  return parseEvaluationSummary(
    await request(`/api/v1/evaluations`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    }),
  );
}

export async function cancelEvaluation(runId: string): Promise<EvaluationSummary> {
  return parseEvaluationSummary(
    await request(`/api/v1/evaluations/${runId}/cancel`, { method: "POST" }),
  );
}

export interface ResumeBudgetRequest {
  additional_budget_cny: number;
  budget_version: number;
}

export interface ResumeBudgetResponse extends EvaluationSummary {
  resumed: boolean;
  attempts_created: number;
}

export async function resumeEvaluationBudget(
  runId: string,
  body: ResumeBudgetRequest,
): Promise<ResumeBudgetResponse> {
  const payload = await request(`/api/v1/evaluations/${runId}/resume-budget`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const summary = parseEvaluationSummary(payload);
  const record = payload as Record<string, unknown>;
  return {
    ...summary,
    resumed: Boolean(record.resumed),
    attempts_created: typeof record.attempts_created === "number" ? record.attempts_created : 0,
  };
}


function readFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readCountOrZero(value: unknown): number {
  const parsed = readFiniteNumber(value);
  return parsed !== null && parsed >= 0 ? parsed : 0;
}

function parseIssues(value: unknown): PreflightIssue[] {
  return Array.isArray(value)
    ? value.map((entry) => {
        const item = (entry ?? {}) as Record<string, unknown>;
        return {
          code: readString(item.code),
          message: readString(item.message),
          field: readOptionalString(item.field) ?? undefined,
          details: isRecord(item.details) ? item.details : undefined,
        };
      })
    : [];
}

function parseCostRange(value: unknown): CostRange | null {
  if (!isRecord(value)) return null;
  const low = typeof value.low === "string" ? value.low : null;
  const high = typeof value.high === "string" ? value.high : null;
  return low && high ? { low, high } : null;
}

function parseCostEstimate(value: unknown): CostEstimate | null {
  if (!isRecord(value)) return null;
  const model = parseCostRange(value.model_cny);
  const total = parseCostRange(value.total_cny);
  if (!model || !total) return null;
  const duration = isRecord(value.estimated_duration_seconds)
    ? value.estimated_duration_seconds
    : {};
  return {
    currency: readString(value.currency) || "CNY",
    model_cny: model,
    runner_cny: parseCostRange(value.runner_cny),
    total_cny: total,
    estimated_duration_seconds: {
      low: readCountOrZero(duration.low),
      high: readCountOrZero(duration.high),
    },
    basis: readString(value.basis),
    assumptions: isRecord(value.assumptions)
      ? Object.fromEntries(
          Object.entries(value.assumptions).filter(
            (entry): entry is [string, number] => typeof entry[1] === "number",
          ),
        )
      : {},
  };
}

function parseExecutionPolicy(value: unknown): ResolvedExecutionPolicy | null {
  if (!isRecord(value)) return null;
  return {
    concurrency: readCountOrZero(value.concurrency),
    case_timeout_seconds: readCountOrZero(value.case_timeout_seconds),
    max_attempts: readCountOrZero(value.max_attempts),
    model_budget_cny: readFiniteNumber(value.model_budget_cny),
    shard_size: readCountOrZero(value.shard_size),
    execution_image_digest: readOptionalString(value.execution_image_digest),
    scoring_image_digest: readOptionalString(value.scoring_image_digest),
  };
}

function parseCaseSelection(value: unknown): CaseSelection | null {
  if (!isRecord(value)) return null;
  const mode: CaseSelectionMode =
    value.mode === "count" || value.mode === "cases" ? value.mode : "all";
  return {
    mode,
    count: readFiniteNumber(value.count),
    case_keys: Array.isArray(value.case_keys)
      ? value.case_keys.filter((key): key is string => typeof key === "string")
      : null,
  };
}

function parsePreparationSummary(value: unknown): PreparationSummary | null {
  if (!isRecord(value)) return null;
  const dataset = isRecord(value.dataset) ? value.dataset : null;
  const execution = parseExecutionPolicy(value.execution);
  if (!dataset || !execution) return null;
  const runtime = isRecord(value.runtime) ? value.runtime : {};
  const model = isRecord(value.model) ? value.model : {};
  const pool = isRecord(value.resource_pool) ? value.resource_pool : {};
  const resolved = isRecord(value.resolved) ? value.resolved : {};
  const assetSnapshot = isRecord(value.asset_snapshot) ? value.asset_snapshot : null;
  return {
    dataset: {
      id: readString(dataset.id),
      name: readString(dataset.name),
      version: readString(dataset.version),
      adapter_key: readString(dataset.adapter_key),
      case_count: readCountOrZero(dataset.case_count),
      source_revision: readString(dataset.source_revision),
      execution_image_digest: readOptionalString(dataset.execution_image_digest),
      scoring_image_digest: readOptionalString(dataset.scoring_image_digest),
    },
    case_count: readCountOrZero(value.case_count),
    estimated_job_count: readCountOrZero(value.estimated_job_count),
    runtime: {
      key: readString(runtime.key),
      version: readOptionalString(runtime.version),
    },
    asset_snapshot: assetSnapshot
      ? {
          id: readString(assetSnapshot.id),
          source_id: readOptionalString(assetSnapshot.source_id),
          requested_ref: readOptionalString(assetSnapshot.requested_ref),
          resolved_commit: readOptionalString(assetSnapshot.resolved_commit),
          asset_count: readFiniteNumber(assetSnapshot.asset_count),
        }
      : null,
    model: {
      config_id: readString(model.config_id),
      display_name: readOptionalString(model.display_name),
      provider: readOptionalString(model.provider),
      model_name: readOptionalString(model.model_name),
    },
    resource_pool: {
      id: readString(pool.id),
      name: readOptionalString(pool.name),
      provider_key: readOptionalString(pool.provider_key),
      max_capacity: readFiniteNumber(pool.max_capacity),
      online_runners: readCountOrZero(pool.online_runners),
      price_cny: readOptionalString(pool.price_cny),
    },
    execution,
    resolved: {
      effective_concurrency: readCountOrZero(resolved.effective_concurrency),
      execution_image_digest: readOptionalString(resolved.execution_image_digest),
      scoring_image_digest: readOptionalString(resolved.scoring_image_digest),
      estimated_job_count: readCountOrZero(resolved.estimated_job_count),
    },
    cost_estimate: parseCostEstimate(value.cost_estimate),
    warnings: parseIssues(value.warnings),
  };
}

export function parsePrepare(payload: unknown): PrepareResponse {
  if (!isRecord(payload) || typeof payload.ready !== "boolean") {
    throw new ApiError("控制面返回了无法识别的预检结果", { code: "invalid_response" });
  }
  return {
    ready: payload.ready,
    blockers: parseIssues(payload.blockers),
    warnings: parseIssues(payload.warnings),
    asset_snapshot: isRecord(payload.asset_snapshot)
      ? (payload.asset_snapshot as unknown as FrozenAssetSnapshot)
      : null,
    preparation_id: readOptionalString(payload.preparation_id),
    expires_at: readOptionalString(payload.expires_at),
    snapshot_digest: readOptionalString(payload.snapshot_digest),
    case_count: readCountOrZero(payload.case_count),
    estimated_job_count: readCountOrZero(payload.estimated_job_count),
    resolved_commit: readOptionalString(payload.resolved_commit),
    effective_concurrency: readCountOrZero(payload.effective_concurrency),
    execution_image_digest: readOptionalString(payload.execution_image_digest),
    scoring_image_digest: readOptionalString(payload.scoring_image_digest),
    case_selection: parseCaseSelection(payload.case_selection),
    execution: parseExecutionPolicy(payload.execution),
    cost_estimate: parseCostEstimate(payload.cost_estimate),
    summary: parsePreparationSummary(payload.summary),
  };
}

export async function prepareEvaluation(body: PrepareRequest): Promise<PrepareResponse> {
  return parsePrepare(
    await request(`/api/v1/evaluations/prepare`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function fetchEvaluationResults(
  runId: string,
  signal?: AbortSignal,
): Promise<{ is_final: boolean; items: MetricResult[] }> {
  const payload = await request(`/api/v1/eval-runs/${runId}/results`, { signal });
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的结果数据", { code: "invalid_response" });
  }
  return {
    is_final: Boolean(payload.is_final),
    items: payload.items.map((entry) => {
      const item = (entry ?? {}) as Record<string, unknown>;
      return {
        metric_key: readString(item.metric_key),
        metric_version: readCount(item.metric_version, "metric_version"),
        display_name: readString(item.display_name),
        description: readOptionalString(item.description),
        formula_text: readOptionalString(item.formula_text),
        display_kind: readString(item.display_kind),
        unit: readOptionalString(item.unit),
        direction: readString(item.direction),
        denominator_policy: readOptionalString(item.denominator_policy),
        value: item.value,
        numerator: readOptionalString(item.numerator),
        denominator: readOptionalString(item.denominator),
        is_final: Boolean(item.is_final),
        generated_at: readOptionalString(item.generated_at),
      };
    }),
  };
}

function parseNumberRecord(value: unknown): Record<string, number> | null {
  if (!isRecord(value)) return null;
  const entries = Object.entries(value).filter(
    (entry): entry is [string, number] =>
      typeof entry[1] === "number" && Number.isFinite(entry[1]),
  );
  return entries.length > 0 ? Object.fromEntries(entries) : null;
}

function parseDeltaValue(value: unknown): number | Record<string, number> | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return parseNumberRecord(value);
}

function parseImprovement(
  value: unknown,
): boolean | Record<string, boolean | null> | null {
  if (typeof value === "boolean") return value;
  if (isRecord(value)) {
    const entries = Object.entries(value).map(
      ([key, item]) => [key, typeof item === "boolean" ? item : null] as const,
    );
    return entries.length > 0 ? (Object.fromEntries(entries) as Record<string, boolean | null>) : null;
  }
  return null;
}

function parseStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function parseBaselineCandidate(value: unknown): BaselineCandidate {
  const item = (value ?? {}) as Record<string, unknown>;
  return {
    id: readString(item.id),
    name: readString(item.name),
    status: readString(item.status),
    created_at: readOptionalString(item.created_at),
    finished_at: readOptionalString(item.finished_at),
    dataset_version_id: readOptionalString(item.dataset_version_id),
    dataset_label: readString(item.dataset_label),
    runtime_key: readOptionalString(item.runtime_key),
    runtime_version: readOptionalString(item.runtime_version),
    model_name: readString(item.model_name),
    asset_resolved_commit: readOptionalString(item.asset_resolved_commit),
    case_count: readCountOrZero(item.case_count),
    comparable: Boolean(item.comparable),
    reasons: parseStringList(item.reasons),
  };
}

function parseBaselineSummary(value: unknown): BaselineSummary | null {
  if (!isRecord(value)) return null;
  return {
    id: readString(value.id),
    name: readString(value.name),
    status: readString(value.status),
    created_at: readOptionalString(value.created_at),
    finished_at: readOptionalString(value.finished_at),
    dataset_version_id: readOptionalString(value.dataset_version_id),
    dataset_label: readString(value.dataset_label),
    runtime_key: readOptionalString(value.runtime_key),
    runtime_version: readOptionalString(value.runtime_version),
    model_name: readString(value.model_name),
    asset_resolved_commit: readOptionalString(value.asset_resolved_commit),
    resource_pool_id: readOptionalString(value.resource_pool_id),
    case_count: readCountOrZero(value.case_count),
    case_digest: readString(value.case_digest),
  };
}

function parseMetricDelta(value: unknown): MetricDelta {
  const item = (value ?? {}) as Record<string, unknown>;
  return {
    metric_key: readString(item.metric_key),
    metric_version: readFiniteNumber(item.metric_version),
    baseline_metric_version: readFiniteNumber(item.baseline_metric_version),
    display_name: readString(item.display_name),
    description: readOptionalString(item.description),
    formula_text: readOptionalString(item.formula_text),
    display_kind: readString(item.display_kind),
    unit: readOptionalString(item.unit),
    direction: readString(item.direction),
    denominator_policy: readOptionalString(item.denominator_policy),
    current_value: item.current_value,
    baseline_value: item.baseline_value,
    current_numerator: readOptionalString(item.current_numerator),
    current_denominator: readOptionalString(item.current_denominator),
    baseline_numerator: readOptionalString(item.baseline_numerator),
    baseline_denominator: readOptionalString(item.baseline_denominator),
    delta: parseDeltaValue(item.delta),
    delta_ratio: parseDeltaValue(item.delta_ratio),
    improvement: parseImprovement(item.improvement),
    comparable: Boolean(item.comparable),
    incomparable_reason: readOptionalString(item.incomparable_reason),
    current_is_final: Boolean(item.current_is_final),
    baseline_is_final: Boolean(item.baseline_is_final),
  };
}

function parseConfigurationDifference(value: unknown): ConfigurationDifference {
  const item = (value ?? {}) as Record<string, unknown>;
  return {
    key: readString(item.key),
    label: readString(item.label),
    category: readString(item.category),
    current: readString(item.current),
    baseline: readString(item.baseline),
    changed: Boolean(item.changed),
    incomparable: Boolean(item.incomparable),
    note: readOptionalString(item.note),
  };
}

export function parseComparison(payload: unknown): BaselineComparison {
  if (!isRecord(payload) || typeof payload.run_id !== "string") {
    throw new ApiError("控制面返回了无法识别的基线对比数据", { code: "invalid_response" });
  }
  const emptyState = isRecord(payload.empty_state)
    ? {
        code: readString(payload.empty_state.code),
        message: readString(payload.empty_state.message),
      }
    : null;
  const recommendation = isRecord(payload.recommendation)
    ? {
        tier: readString(payload.recommendation.tier),
        reason: readString(payload.recommendation.reason),
      }
    : null;
  const caseSet = isRecord(payload.case_set) ? payload.case_set : {};
  return {
    run_id: payload.run_id,
    run_name: readString(payload.run_name),
    status: readString(payload.status),
    is_final: Boolean(payload.is_final),
    manual: Boolean(payload.manual),
    baseline_run_id: readOptionalString(payload.baseline_run_id),
    recommendation,
    baseline: parseBaselineSummary(payload.baseline),
    candidates: Array.isArray(payload.candidates)
      ? payload.candidates.map(parseBaselineCandidate)
      : [],
    comparable: Boolean(payload.comparable),
    incomparable_reasons: parseStringList(payload.incomparable_reasons),
    case_set: {
      comparable: Boolean(caseSet.comparable),
      current_count: readCountOrZero(caseSet.current_count),
      baseline_count: readCountOrZero(caseSet.baseline_count),
    },
    metric_deltas: Array.isArray(payload.metric_deltas)
      ? payload.metric_deltas.map(parseMetricDelta)
      : [],
    configuration_differences: Array.isArray(payload.configuration_differences)
      ? payload.configuration_differences.map(parseConfigurationDifference)
      : [],
    empty_state: emptyState,
    generated_at: readOptionalString(payload.generated_at),
  };
}

export async function fetchEvaluationComparison(
  runId: string,
  baselineRunId?: string | null,
  signal?: AbortSignal,
): Promise<BaselineComparison> {
  const params = new URLSearchParams();
  if (baselineRunId) params.set("baseline_run_id", baselineRunId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return parseComparison(
    await request(`/api/v1/eval-runs/${runId}/compare${suffix}`, { signal }),
  );
}

export async function fetchCaseTests(
  caseRunId: string,
  attempt?: number | null,
  signal?: AbortSignal,
): Promise<{ attempt: number | null; items: CaseTestResult[]; summary: Record<string, number> }> {
  const params = new URLSearchParams();
  if (typeof attempt === "number") params.set("attempt", String(attempt));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const payload = await request(`/api/v1/eval-case-runs/${caseRunId}/tests${suffix}`, { signal });
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的测试结果", { code: "invalid_response" });
  }
  return {
    attempt: typeof payload.attempt === "number" ? payload.attempt : null,
    summary: isRecord(payload.summary)
      ? Object.fromEntries(
          Object.entries(payload.summary).filter((entry): entry is [string, number] => typeof entry[1] === "number"),
        )
      : {},
    items: payload.items.map((entry) => {
      const item = (entry ?? {}) as Record<string, unknown>;
      return {
        id: readString(item.id),
        suite_name: readString(item.suite_name),
        test_name: readString(item.test_name),
        outcome: readString(item.outcome),
        native_status: readString(item.native_status),
        test_group: readString(item.test_group),
        expected: Boolean(item.expected),
        duration_ms: typeof item.duration_ms === "number" ? item.duration_ms : null,
        file_path: readOptionalString(item.file_path),
        line_no: typeof item.line_no === "number" ? item.line_no : null,
        failure_message: readOptionalString(item.failure_message),
        log_object_key: readOptionalString(item.log_object_key),
      };
    }),
  };
}

/** Stream SSE events; replays from the DB using the snapshot cursor. */
export async function streamEvaluationEvents(
  runId: string,
  afterId: number,
  onEvent: (event: EvaluationEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(
    `/api/v1/evaluations/${runId}/events/stream?after_id=${encodeURIComponent(String(afterId))}`,
    { signal, credentials: "same-origin", headers: { Accept: "text/event-stream" } },
  );
  if (!response.ok || !response.body) {
    throw new ApiError(`事件流连接失败（HTTP ${response.status}）`, {
      status: response.status,
      code: "stream_failed",
    });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
      const dataLines: string[] = [];
      let type = "message";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) type = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;
      try {
        const item = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
        onEvent({
          id: readCount(item.id, "id"),
          event_type: readString(item.event_type) || type,
          case_run_id: readOptionalString(item.case_run_id),
          attempt_no: typeof item.attempt_no === "number" ? item.attempt_no : null,
          sequence: readCount(item.sequence, "sequence"),
          payload: isRecord(item.payload) ? item.payload : {},
          occurred_at: readOptionalString(item.occurred_at),
        });
      } catch {
        // Ignore a partial/malformed event; the snapshot remains authoritative.
      }
    }
  }
}

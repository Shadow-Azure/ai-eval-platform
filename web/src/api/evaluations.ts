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
  runtime_key?: string | null;
  runtime_version?: string | null;
  model_name?: string | null;
  model_budget_usd?: string | null;
  budget_spent_usd?: string | null;
  budget_remaining_usd?: string | null;
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

export interface EvaluationDetail extends EvaluationSummary {
  cases: EvaluationCase[];
  shards: EvaluationShard[];
  asset_snapshot?: FrozenAssetSnapshot | null;
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

export interface EvaluationListResponse {
  status: string;
  total: number;
  items: EvaluationSummary[];
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
}

export interface PreflightIssue {
  code: string;
  message: string;
  field?: string;
  details?: Record<string, unknown>;
}

export interface PrepareResponse {
  ready: boolean;
  blockers: PreflightIssue[];
  warnings: PreflightIssue[];
  asset_snapshot?: FrozenAssetSnapshot | null;
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
  asset_source_id?: string;
  requested_ref?: string;
  asset_paths?: string[];
  asset_types?: Record<string, string>;
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
    runtime_key: readOptionalString(payload.runtime_key),
    runtime_version: readOptionalString(payload.runtime_version),
    model_name: readOptionalString(payload.model_name),
    model_budget_usd: readOptionalString(payload.model_budget_usd),
    budget_spent_usd: readOptionalString(payload.budget_spent_usd),
    budget_remaining_usd: readOptionalString(payload.budget_remaining_usd),
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
    case_counts: readNumberMap(record.case_counts),
    trace_counts: readNumberMap(record.trace_counts),
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

function readNumberMap(value: unknown): Record<string, number> | undefined {
  if (!isRecord(value)) return undefined;
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [string, number] => typeof entry[1] === "number",
    ),
  );
}

export async function fetchEvaluations(signal?: AbortSignal): Promise<EvaluationListResponse> {
  return parseEvaluationList(await request(`/api/v1/evaluations?limit=50`, { signal }));
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
): Promise<EvaluationSummary> {
  return parseEvaluationSummary(
    await request(`/api/v1/evaluations`, {
      method: "POST",
      headers: { "content-type": "application/json" },
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
  additional_budget_usd: number;
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


export function parsePrepare(payload: unknown): PrepareResponse {
  if (!isRecord(payload) || typeof payload.ready !== "boolean") {
    throw new ApiError("控制面返回了无法识别的预检结果", { code: "invalid_response" });
  }
  const issues = (value: unknown): PreflightIssue[] =>
    Array.isArray(value)
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
  return {
    ready: payload.ready,
    blockers: issues(payload.blockers),
    warnings: issues(payload.warnings),
    asset_snapshot: isRecord(payload.asset_snapshot)
      ? (payload.asset_snapshot as unknown as FrozenAssetSnapshot)
      : null,
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

import { ApiError } from "./types";
import { request } from "./evaluations";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function optStr(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ApiError(`控制面响应中的 ${field} 无效`, { code: "invalid_response" });
  }
  return value;
}

function optNum(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export interface CaseTestSummary {
  passed: number;
  failed: number;
  skipped: number;
  error: number;
  f2p_failed: number;
  p2p_failed: number;
}

export function emptyTestSummary(): CaseTestSummary {
  return { passed: 0, failed: 0, skipped: 0, error: 0, f2p_failed: 0, p2p_failed: 0 };
}

function parseTestSummary(value: unknown): CaseTestSummary {
  const record = isRecord(value) ? value : {};
  const summary = emptyTestSummary();
  for (const key of Object.keys(summary) as Array<keyof CaseTestSummary>) {
    summary[key] = typeof record[key] === "number" ? record[key] : 0;
  }
  return summary;
}

export interface EvalCaseRow {
  id: string;
  case_key: string;
  ordinal: number;
  repo: string | null;
  execution_status: string;
  result_status: string;
  trace_status: string;
  latest_attempt_no: number;
  duration_ms: number | null;
  error_code: string | null;
  error_summary: string | null;
  metrics: Record<string, unknown>;
  test_summary: CaseTestSummary;
  updated_at: string | null;
}

export interface EvalCaseCounts {
  all: number;
  resolved: number;
  unresolved: number;
  running: number;
  failed: number;
  queued: number;
}

export const EMPTY_CASE_COUNTS: EvalCaseCounts = {
  all: 0,
  resolved: 0,
  unresolved: 0,
  running: 0,
  failed: 0,
  queued: 0,
};

export interface EvalCasePage {
  run_id: string;
  items: EvalCaseRow[];
  total: number;
  counts: EvalCaseCounts;
  limit: number;
  offset: number;
}

export interface EvalCaseFilters {
  status?: string;
  q?: string;
  execution_status?: string;
  result_status?: string;
  trace_status?: string;
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

function parseCase(row: unknown): EvalCaseRow {
  const item = isRecord(row) ? row : {};
  return {
    id: str(item.id),
    case_key: str(item.case_key),
    ordinal: num(item.ordinal, "ordinal"),
    repo: optStr(item.repo),
    execution_status: str(item.execution_status, "queued"),
    result_status: str(item.result_status, "not_scored"),
    trace_status: str(item.trace_status, "pending"),
    latest_attempt_no: num(item.latest_attempt_no, "latest_attempt_no"),
    duration_ms: optNum(item.duration_ms),
    error_code: optStr(item.error_code),
    error_summary: optStr(item.error_summary),
    metrics: isRecord(item.metrics) ? item.metrics : {},
    test_summary: parseTestSummary(item.test_summary),
    updated_at: optStr(item.updated_at),
  };
}

export async function fetchEvaluationCases(
  runId: string,
  filters: EvalCaseFilters = {},
  signal?: AbortSignal,
): Promise<EvalCasePage> {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? 20));
  params.set("offset", String(filters.offset ?? 0));
  if (filters.status && filters.status !== "all") params.set("status", filters.status);
  if (filters.q?.trim()) params.set("q", filters.q.trim());
  if (filters.execution_status) params.set("execution_status", filters.execution_status);
  if (filters.result_status) params.set("result_status", filters.result_status);
  if (filters.trace_status) params.set("trace_status", filters.trace_status);
  if (filters.sort) params.set("sort", filters.sort);
  if (filters.order) params.set("order", filters.order);
  const payload = await request(`/api/v1/eval-runs/${runId}/cases?${params.toString()}`, {
    signal,
  });
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的用例列表", { code: "invalid_response" });
  }
  const counts = isRecord(payload.counts) ? payload.counts : {};
  const page = isRecord(payload.page_info) ? payload.page_info : {};
  return {
    run_id: str(payload.run_id),
    items: payload.items.map(parseCase),
    total: num(payload.total, "total"),
    counts: {
      all: typeof counts.all === "number" ? counts.all : 0,
      resolved: typeof counts.resolved === "number" ? counts.resolved : 0,
      unresolved: typeof counts.unresolved === "number" ? counts.unresolved : 0,
      running: typeof counts.running === "number" ? counts.running : 0,
      failed: typeof counts.failed === "number" ? counts.failed : 0,
      queued: typeof counts.queued === "number" ? counts.queued : 0,
    },
    limit: typeof page.limit === "number" ? page.limit : (filters.limit ?? 20),
    offset: typeof page.offset === "number" ? page.offset : (filters.offset ?? 0),
  };
}

export interface EvalAttempt {
  id: string;
  attempt_no: number;
  status: string;
  phase: string | null;
  dispatch_status: string | null;
  runner_id: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  error_code: string | null;
  error_summary: string | null;
  retry_reason: string | null;
  test_summary: CaseTestSummary;
}

export interface EvalRetryState {
  retryable: boolean;
  reason: string | null;
}

export interface EvalCaseDetail {
  case: EvalCaseRow;
  run: {
    id: string;
    name: string;
    status: string;
    max_attempts: number;
    case_timeout_seconds: number;
    requested_concurrency: number;
    effective_concurrency: number | null;
    total_cases: number;
  };
  attempts: EvalAttempt[];
  retry: EvalRetryState;
  tests_attempt: number | null;
}

function parseAttempt(row: unknown): EvalAttempt {
  const item = isRecord(row) ? row : {};
  return {
    id: str(item.id),
    attempt_no: num(item.attempt_no, "attempt_no"),
    status: str(item.status, "pending"),
    phase: optStr(item.phase),
    dispatch_status: optStr(item.dispatch_status),
    runner_id: optStr(item.runner_id),
    started_at: optStr(item.started_at),
    ended_at: optStr(item.ended_at),
    duration_ms: optNum(item.duration_ms),
    error_code: optStr(item.error_code),
    error_summary: optStr(item.error_summary),
    retry_reason: optStr(item.retry_reason),
    test_summary: parseTestSummary(item.test_summary),
  };
}

function parseRetryState(value: unknown): EvalRetryState {
  const item = isRecord(value) ? value : {};
  return { retryable: Boolean(item.retryable), reason: optStr(item.reason) };
}

export async function fetchEvaluationCase(
  caseRunId: string,
  signal?: AbortSignal,
): Promise<EvalCaseDetail> {
  const payload = await request(`/api/v1/eval-case-runs/${caseRunId}`, { signal });
  if (!isRecord(payload) || !isRecord(payload.case) || !isRecord(payload.run)) {
    throw new ApiError("控制面返回了无法识别的用例详情", { code: "invalid_response" });
  }
  const run = payload.run;
  return {
    case: parseCase(payload.case),
    run: {
      id: str(run.id),
      name: str(run.name),
      status: str(run.status),
      max_attempts: num(run.max_attempts, "max_attempts"),
      case_timeout_seconds: num(run.case_timeout_seconds, "case_timeout_seconds"),
      requested_concurrency: num(run.requested_concurrency, "requested_concurrency"),
      effective_concurrency: optNum(run.effective_concurrency),
      total_cases: num(run.total_cases, "total_cases"),
    },
    attempts: Array.isArray(payload.attempts) ? payload.attempts.map(parseAttempt) : [],
    retry: parseRetryState(payload.retry),
    tests_attempt: optNum(payload.tests_attempt),
  };
}

export interface EvalConfigMetric {
  id: string;
  metric_key: string;
  version: number;
  display_name: string;
  description: string | null;
  formula_text: string | null;
  display_kind: string;
  unit: string | null;
  direction: string;
  denominator_policy: string | null;
  config: Record<string, unknown>;
}

export interface EvalConfigSnapshot {
  run_id: string;
  name: string;
  status: string;
  created_by: string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  dataset: {
    dataset_id: string;
    dataset_name: string | null;
    dataset_slug: string | null;
    adapter_key: string | null;
    task_family: string | null;
    environment_kind: string | null;
    version_id: string;
    version: string;
    status: string;
    case_count: number;
    source_uri: string;
    source_revision: string;
    adapter_version: string;
    environment_image: string | null;
    execution_image_digest: string | null;
    scoring_image_digest: string | null;
    created_at: string | null;
  } | null;
  runtime: { key: string | null; version: string | null };
  assets: {
    snapshot_id: string | null;
    source_id: string | null;
    requested_ref: string | null;
    resolved_commit: string | null;
    tree_digest: string | null;
    bundle_digest: string | null;
    bundle_size: number | null;
    file_count: number | null;
    asset_count: number | null;
    runtime_key: string | null;
    items: Array<Record<string, unknown>>;
  };
  model: {
    id: string;
    display_name: string;
    provider: string;
    model_name: string;
    api_style: string;
    base_url: string;
    is_temporary: boolean;
  } | null;
  resource_pool: {
    id: string;
    name: string;
    provider_key: string;
    provisioning_mode: string;
    min_capacity: number;
    max_capacity: number;
    price_usd: string | null;
    price_threshold_usd: string | null;
    capacity_reason: string | null;
    status: string;
  } | null;
  execution: {
    shard_size: number;
    requested_concurrency: number;
    effective_concurrency: number | null;
    queue_reason: string | null;
    case_timeout_seconds: number;
    max_attempts: number;
    execution_image_digest: string | null;
    scoring_image_digest: string | null;
    task_manifest_digest: string | null;
  };
  consistency: {
    effective_concurrency: number | null;
    warnings: Array<Record<string, unknown>>;
    blockers: Array<Record<string, unknown>>;
  };
  budget: {
    model_budget_usd: string | null;
    budget_spent_usd: string | null;
    budget_remaining_usd: string | null;
    budget_version: number;
    pause_reason: string | null;
  };
  metrics: EvalConfigMetric[];
}

function parseConfigMetric(row: unknown): EvalConfigMetric {
  const item = isRecord(row) ? row : {};
  return {
    id: str(item.id),
    metric_key: str(item.metric_key),
    version: typeof item.version === "number" ? item.version : 1,
    display_name: str(item.display_name),
    description: optStr(item.description),
    formula_text: optStr(item.formula_text),
    display_kind: str(item.display_kind, "number"),
    unit: optStr(item.unit),
    direction: str(item.direction, "higher_is_better"),
    denominator_policy: optStr(item.denominator_policy),
    config: isRecord(item.config) ? item.config : {},
  };
}

function parseConfigDataset(value: unknown): EvalConfigSnapshot["dataset"] {
  if (!isRecord(value)) return null;
  return {
    dataset_id: str(value.dataset_id),
    dataset_name: optStr(value.dataset_name),
    dataset_slug: optStr(value.dataset_slug),
    adapter_key: optStr(value.adapter_key),
    task_family: optStr(value.task_family),
    environment_kind: optStr(value.environment_kind),
    version_id: str(value.version_id),
    version: str(value.version),
    status: str(value.status),
    case_count: typeof value.case_count === "number" ? value.case_count : 0,
    source_uri: str(value.source_uri),
    source_revision: str(value.source_revision),
    adapter_version: str(value.adapter_version),
    environment_image: optStr(value.environment_image),
    execution_image_digest: optStr(value.execution_image_digest),
    scoring_image_digest: optStr(value.scoring_image_digest),
    created_at: optStr(value.created_at),
  };
}

export async function fetchEvaluationConfig(
  runId: string,
  signal?: AbortSignal,
): Promise<EvalConfigSnapshot> {
  const payload = await request(`/api/v1/eval-runs/${runId}/config`, { signal });
  if (!isRecord(payload) || !isRecord(payload.runtime) || !isRecord(payload.execution)) {
    throw new ApiError("控制面返回了无法识别的配置快照", { code: "invalid_response" });
  }
  const runtime = payload.runtime;
  const execution = payload.execution;
  const assets = isRecord(payload.assets) ? payload.assets : {};
  const consistency = isRecord(payload.consistency) ? payload.consistency : {};
  const budget = isRecord(payload.budget) ? payload.budget : {};
  const model = isRecord(payload.model) ? payload.model : null;
  const pool = isRecord(payload.resource_pool) ? payload.resource_pool : null;
  return {
    run_id: str(payload.run_id),
    name: str(payload.name),
    status: str(payload.status),
    created_by: str(payload.created_by),
    created_at: optStr(payload.created_at),
    started_at: optStr(payload.started_at),
    finished_at: optStr(payload.finished_at),
    dataset: parseConfigDataset(payload.dataset),
    runtime: { key: optStr(runtime.key), version: optStr(runtime.version) },
    assets: {
      snapshot_id: optStr(assets.snapshot_id),
      source_id: optStr(assets.source_id),
      requested_ref: optStr(assets.requested_ref),
      resolved_commit: optStr(assets.resolved_commit),
      tree_digest: optStr(assets.tree_digest),
      bundle_digest: optStr(assets.bundle_digest),
      bundle_size: optNum(assets.bundle_size),
      file_count: optNum(assets.file_count),
      asset_count: optNum(assets.asset_count),
      runtime_key: optStr(assets.runtime_key),
      items: Array.isArray(assets.items)
        ? assets.items.filter(isRecord)
        : [],
    },
    model: model
      ? {
          id: str(model.id),
          display_name: str(model.display_name),
          provider: str(model.provider),
          model_name: str(model.model_name),
          api_style: str(model.api_style),
          base_url: str(model.base_url),
          is_temporary: Boolean(model.is_temporary),
        }
      : null,
    resource_pool: pool
      ? {
          id: str(pool.id),
          name: str(pool.name),
          provider_key: str(pool.provider_key),
          provisioning_mode: str(pool.provisioning_mode),
          min_capacity: typeof pool.min_capacity === "number" ? pool.min_capacity : 0,
          max_capacity: typeof pool.max_capacity === "number" ? pool.max_capacity : 0,
          price_usd: optStr(pool.price_usd),
          price_threshold_usd: optStr(pool.price_threshold_usd),
          capacity_reason: optStr(pool.capacity_reason),
          status: str(pool.status),
        }
      : null,
    execution: {
      shard_size: num(execution.shard_size, "shard_size"),
      requested_concurrency: num(execution.requested_concurrency, "requested_concurrency"),
      effective_concurrency: optNum(execution.effective_concurrency),
      queue_reason: optStr(execution.queue_reason),
      case_timeout_seconds: num(execution.case_timeout_seconds, "case_timeout_seconds"),
      max_attempts: num(execution.max_attempts, "max_attempts"),
      execution_image_digest: optStr(execution.execution_image_digest),
      scoring_image_digest: optStr(execution.scoring_image_digest),
      task_manifest_digest: optStr(execution.task_manifest_digest),
    },
    consistency: {
      effective_concurrency: optNum(consistency.effective_concurrency),
      warnings: Array.isArray(consistency.warnings)
        ? consistency.warnings.filter(isRecord)
        : [],
      blockers: Array.isArray(consistency.blockers)
        ? consistency.blockers.filter(isRecord)
        : [],
    },
    budget: {
      model_budget_usd: optStr(budget.model_budget_usd),
      budget_spent_usd: optStr(budget.budget_spent_usd),
      budget_remaining_usd: optStr(budget.budget_remaining_usd),
      budget_version:
        typeof budget.budget_version === "number" ? budget.budget_version : 1,
      pause_reason: optStr(budget.pause_reason),
    },
    metrics: Array.isArray(payload.metrics) ? payload.metrics.map(parseConfigMetric) : [],
  };
}

export interface EvalRetryResult {
  case: EvalCaseRow;
  attempt: EvalAttempt;
  retry: EvalRetryState;
}

export async function retryEvaluationCase(caseRunId: string): Promise<EvalRetryResult> {
  const payload = await request(`/api/v1/eval-case-runs/${caseRunId}/retry`, {
    method: "POST",
  });
  if (!isRecord(payload) || !isRecord(payload.case) || !isRecord(payload.attempt)) {
    throw new ApiError("控制面返回了无法识别的重试结果", { code: "invalid_response" });
  }
  return {
    case: parseCase(payload.case),
    attempt: parseAttempt(payload.attempt),
    retry: parseRetryState(payload.retry),
  };
}

export interface EvalBulkRetryResult {
  run_id: string;
  retried: number;
  skipped: number;
}

export async function retryFailedEvaluationCases(runId: string): Promise<EvalBulkRetryResult> {
  const payload = await request(`/api/v1/eval-runs/${runId}/retry-failed`, {
    method: "POST",
  });
  if (!isRecord(payload)) {
    throw new ApiError("控制面返回了无法识别的重试结果", { code: "invalid_response" });
  }
  return {
    run_id: str(payload.run_id),
    retried: typeof payload.retried === "number" ? payload.retried : 0,
    skipped: typeof payload.skipped === "number" ? payload.skipped : 0,
  };
}

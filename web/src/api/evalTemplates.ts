import { ApiError } from "./types";
import { request, type CaseSelection, type CostEstimate, type ExecutionPolicy } from "./evaluations";

export interface EvalTemplateExecution {
  concurrency: number;
  case_timeout_seconds?: number | null;
  max_attempts?: number | null;
  model_budget_cny?: number | null;
  shard_size?: number | null;
}

/** M8 #382: template-level bad-case reporting target. The token is write-only,
 * so a response can only state whether one is stored (`has_token`). */
export interface EvalTemplateSelfEvolution {
  enabled: boolean;
  base_url: string | null;
  workspace_id: string | null;
  has_token: boolean;
}

/** Write-only payload for create/update; the token is encrypted at rest. */
export interface EvalTemplateSelfEvolutionInput {
  enabled: boolean;
  base_url?: string;
  workspace_id?: string;
  token?: string;
}

export interface EvalTemplateRecentRun {
  id: string;
  name: string;
  status: string;
  template_version: number | null;
  total_cases: number;
  resolved_count: number | null;
  scored_count: number | null;
  resolve_rate: number | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface EvalTemplate {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  version: number;
  launch_count: number;
  last_launched_at: string | null;
  created_by: string;
  created_at: string | null;
  updated_at: string | null;
  dataset_version_id: string;
  dataset_id: string | null;
  dataset_name: string | null;
  dataset_version: string | null;
  dataset_case_count: number;
  case_count: number;
  case_selection: CaseSelection;
  runtime_key: string;
  runtime_version: string | null;
  asset_source_id: string | null;
  asset_source_name: string | null;
  asset_repo_url: string | null;
  asset_ref: string | null;
  asset_paths: string[];
  asset_path_count: number;
  asset_types: Record<string, string>;
  model_config_id: string;
  model_provider: string | null;
  model_display_name: string | null;
  model_name: string | null;
  resource_pool_id: string;
  resource_pool_name: string | null;
  resource_pool_max_capacity: number | null;
  resource_pool_price_cny: string | null;
  execution: EvalTemplateExecution;
  prompt_suffix: string;
  max_turns: number | null;
  agent_timeout_seconds: number | null;
  self_evolution: EvalTemplateSelfEvolution;
  estimated_cost: CostEstimate | null;
  status_url: string;
  result_url: string;
  recent_runs?: EvalTemplateRecentRun[];
}

export interface EvalTemplateListResponse {
  items: EvalTemplate[];
  total: number;
  limit: number;
  offset: number;
}

export interface EvalTemplateListQuery {
  q?: string;
  status?: string;
  dataset_version_id?: string;
  limit?: number;
  offset?: number;
}

export interface CreateEvalTemplateRequest {
  name: string;
  description?: string;
  dataset_version_id: string;
  runtime_key?: string;
  runtime_version?: string;
  asset_source_id?: string;
  asset_ref?: string;
  asset_paths?: string[];
  asset_types?: Record<string, string>;
  model_config_id: string;
  model_name?: string;
  resource_pool_id: string;
  case_selection?: CaseSelection;
  execution?: ExecutionPolicy;
  prompt_suffix?: string;
  max_turns?: number;
  agent_timeout_seconds?: number;
  self_evolution?: EvalTemplateSelfEvolutionInput;
  enabled?: boolean;
}

export interface UpdateEvalTemplateRequest {
  name?: string;
  description?: string | null;
  dataset_version_id?: string;
  asset_source_id?: string | null;
  asset_ref?: string | null;
  asset_paths?: string[];
  model_config_id?: string;
  resource_pool_id?: string;
  case_selection?: CaseSelection;
  execution?: ExecutionPolicy;
  prompt_suffix?: string;
  max_turns?: number | null;
  agent_timeout_seconds?: number | null;
  self_evolution?: EvalTemplateSelfEvolutionInput;
  enabled?: boolean;
}

export interface LaunchTemplateResponse {
  run_id: string;
  template_id: string;
  status: string;
  status_url: string;
  result_url: string;
  created: boolean;
}

export interface EvalRunStatus {
  run_id: string;
  template_id: string | null;
  template_version: number | null;
  status: string;
  terminal: boolean;
  total_cases: number;
  queued_count: number;
  running_count: number;
  succeeded_count: number;
  failed_count: number;
  finished_count: number;
  progress: { total: number; finished: number; percent: number };
  resolved_count: number;
  scored_count: number;
  resolve_rate: number | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  status_url: string;
  result_url: string;
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

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function optionalCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function stringMap(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [string, string] => typeof entry[1] === "string",
    ),
  );
}

function parseCaseSelection(value: unknown): CaseSelection {
  if (!isRecord(value)) return { mode: "all" };
  const mode = value.mode === "count" || value.mode === "cases" ? value.mode : "all";
  return {
    mode,
    count: mode === "count" ? optionalCount(value.count) : null,
    case_keys: mode === "cases" ? stringList(value.case_keys) : null,
  };
}

function parseExecution(value: unknown): EvalTemplateExecution {
  const record = isRecord(value) ? value : {};
  return {
    concurrency: count(record.concurrency) || 1,
    case_timeout_seconds: optionalCount(record.case_timeout_seconds),
    max_attempts: optionalCount(record.max_attempts),
    model_budget_cny: optionalCount(record.model_budget_cny),
    shard_size: optionalCount(record.shard_size),
  };
}

function parseCostRange(value: unknown): { low: string; high: string } | null {
  if (!isRecord(value)) return null;
  return { low: text(value.low), high: text(value.high) };
}

function parseCostEstimate(value: unknown): CostEstimate | null {
  if (!isRecord(value)) return null;
  const total = parseCostRange(value.total_cny);
  const model = parseCostRange(value.model_cny);
  if (!total || !model) return null;
  const duration = isRecord(value.estimated_duration_seconds)
    ? {
        low: count(value.estimated_duration_seconds.low),
        high: count(value.estimated_duration_seconds.high),
      }
    : { low: 0, high: 0 };
  const assumptions = isRecord(value.assumptions)
    ? Object.fromEntries(
        Object.entries(value.assumptions).filter(
          (entry): entry is [string, number] => typeof entry[1] === "number",
        ),
      )
    : {};
  return {
    currency: text(value.currency) || "CNY",
    model_cny: model,
    runner_cny: parseCostRange(value.runner_cny),
    total_cny: total,
    estimated_duration_seconds: duration,
    basis: text(value.basis),
    assumptions,
  };
}

function parseSelfEvolution(value: unknown): EvalTemplateSelfEvolution {
  if (!isRecord(value)) {
    return { enabled: false, base_url: null, workspace_id: null, has_token: false };
  }
  return {
    enabled: value.enabled === true,
    base_url: optionalText(value.base_url),
    workspace_id: optionalText(value.workspace_id),
    has_token: value.has_token === true,
  };
}

function parseRecentRun(value: unknown): EvalTemplateRecentRun {
  const record = isRecord(value) ? value : {};
  return {
    id: text(record.id),
    name: text(record.name),
    status: text(record.status),
    template_version: optionalCount(record.template_version),
    total_cases: count(record.total_cases),
    resolved_count: optionalCount(record.resolved_count),
    scored_count: optionalCount(record.scored_count),
    resolve_rate: optionalCount(record.resolve_rate),
    created_at: optionalText(record.created_at),
    started_at: optionalText(record.started_at),
    finished_at: optionalText(record.finished_at),
    duration_seconds: optionalCount(record.duration_seconds),
  };
}

export function parseEvalTemplate(payload: unknown): EvalTemplate {
  if (!isRecord(payload)) {
    throw new ApiError("控制面返回了无法识别的评测模板数据", { code: "invalid_response" });
  }
  return {
    id: text(payload.id),
    name: text(payload.name),
    description: optionalText(payload.description),
    enabled: payload.enabled !== false,
    version: count(payload.version) || 1,
    launch_count: count(payload.launch_count),
    last_launched_at: optionalText(payload.last_launched_at),
    created_by: text(payload.created_by),
    created_at: optionalText(payload.created_at),
    updated_at: optionalText(payload.updated_at),
    dataset_version_id: text(payload.dataset_version_id),
    dataset_id: optionalText(payload.dataset_id),
    dataset_name: optionalText(payload.dataset_name),
    dataset_version: optionalText(payload.dataset_version),
    dataset_case_count: count(payload.dataset_case_count),
    case_count: count(payload.case_count),
    case_selection: parseCaseSelection(payload.case_selection),
    runtime_key: text(payload.runtime_key) || "claude-code",
    runtime_version: optionalText(payload.runtime_version),
    asset_source_id: optionalText(payload.asset_source_id),
    asset_source_name: optionalText(payload.asset_source_name),
    asset_repo_url: optionalText(payload.asset_repo_url),
    asset_ref: optionalText(payload.asset_ref),
    asset_paths: stringList(payload.asset_paths),
    asset_path_count: count(payload.asset_path_count),
    asset_types: stringMap(payload.asset_types),
    model_config_id: text(payload.model_config_id),
    model_provider: optionalText(payload.model_provider),
    model_display_name: optionalText(payload.model_display_name),
    model_name: optionalText(payload.model_name),
    resource_pool_id: text(payload.resource_pool_id),
    resource_pool_name: optionalText(payload.resource_pool_name),
    resource_pool_max_capacity: optionalCount(payload.resource_pool_max_capacity),
    resource_pool_price_cny: optionalText(payload.resource_pool_price_cny),
    execution: parseExecution(payload.execution),
    prompt_suffix: text(payload.prompt_suffix),
    max_turns: optionalCount(payload.max_turns),
    agent_timeout_seconds: optionalCount(payload.agent_timeout_seconds),
    self_evolution: parseSelfEvolution(payload.self_evolution),
    estimated_cost: parseCostEstimate(payload.estimated_cost),
    status_url: text(payload.status_url),
    result_url: text(payload.result_url),
    recent_runs: Array.isArray(payload.recent_runs)
      ? payload.recent_runs.map(parseRecentRun)
      : undefined,
  };
}

export async function fetchEvalTemplates(
  query: EvalTemplateListQuery,
  signal?: AbortSignal,
): Promise<EvalTemplateListResponse> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.status) params.set("status", query.status);
  if (query.dataset_version_id) params.set("dataset_version_id", query.dataset_version_id);
  params.set("limit", String(query.limit ?? 20));
  params.set("offset", String(query.offset ?? 0));
  const payload = await request(`/api/v1/eval-templates?${params.toString()}`, { signal });
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的模板列表", { code: "invalid_response" });
  }
  return {
    items: payload.items.map(parseEvalTemplate),
    total: count(payload.total),
    limit: count(payload.limit) || 20,
    offset: count(payload.offset),
  };
}

export async function fetchEvalTemplate(
  templateId: string,
  signal?: AbortSignal,
): Promise<EvalTemplate> {
  const payload = await request(`/api/v1/eval-templates/${templateId}`, { signal });
  if (!isRecord(payload)) {
    throw new ApiError("控制面返回了无法识别的模板数据", { code: "invalid_response" });
  }
  return parseEvalTemplate(payload.template);
}

export async function createEvalTemplate(
  body: CreateEvalTemplateRequest,
): Promise<EvalTemplate> {
  const payload = await request("/api/v1/eval-templates", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!isRecord(payload)) {
    throw new ApiError("控制面返回了无法识别的模板数据", { code: "invalid_response" });
  }
  return parseEvalTemplate(payload.template);
}

export async function updateEvalTemplate(
  templateId: string,
  body: UpdateEvalTemplateRequest,
  version?: number,
): Promise<EvalTemplate> {
  const suffix = version ? `?version=${version}` : "";
  const payload = await request(`/api/v1/eval-templates/${templateId}${suffix}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!isRecord(payload)) {
    throw new ApiError("控制面返回了无法识别的模板数据", { code: "invalid_response" });
  }
  return parseEvalTemplate(payload.template);
}

export async function deleteEvalTemplate(templateId: string): Promise<void> {
  await request(`/api/v1/eval-templates/${templateId}`, { method: "DELETE" });
}

export async function launchEvalTemplate(
  templateId: string,
  body: { name?: string; idempotency_key?: string },
  idempotencyKey?: string,
): Promise<LaunchTemplateResponse> {
  const payload = await request(`/api/v1/eval-templates/${templateId}/launch`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!isRecord(payload) || typeof payload.run_id !== "string") {
    throw new ApiError("控制面返回了无法识别的启动结果", { code: "invalid_response" });
  }
  return {
    run_id: payload.run_id,
    template_id: text(payload.template_id) || templateId,
    status: text(payload.status),
    status_url: text(payload.status_url),
    result_url: text(payload.result_url),
    created: payload.created !== false,
  };
}

export async function fetchEvalRunStatus(
  runId: string,
  signal?: AbortSignal,
): Promise<EvalRunStatus> {
  const payload = await request(`/api/v1/eval-runs/${runId}/status`, { signal });
  if (!isRecord(payload)) {
    throw new ApiError("控制面返回了无法识别的运行状态", { code: "invalid_response" });
  }
  const progress = isRecord(payload.progress) ? payload.progress : {};
  return {
    run_id: text(payload.run_id) || runId,
    template_id: optionalText(payload.template_id),
    template_version: optionalCount(payload.template_version),
    status: text(payload.status),
    terminal: payload.terminal === true,
    total_cases: count(payload.total_cases),
    queued_count: count(payload.queued_count),
    running_count: count(payload.running_count),
    succeeded_count: count(payload.succeeded_count),
    failed_count: count(payload.failed_count),
    finished_count: count(payload.finished_count),
    progress: {
      total: count(progress.total),
      finished: count(progress.finished),
      percent: count(progress.percent),
    },
    resolved_count: count(payload.resolved_count),
    scored_count: count(payload.scored_count),
    resolve_rate: optionalCount(payload.resolve_rate),
    started_at: optionalText(payload.started_at),
    finished_at: optionalText(payload.finished_at),
    duration_seconds: optionalCount(payload.duration_seconds),
    status_url: text(payload.status_url),
    result_url: text(payload.result_url),
  };
}

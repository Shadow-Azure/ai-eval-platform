import { ApiError } from "./types";

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
  shard_size: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  idempotency_key: string;
  created_by: string;
}

export interface EvaluationCase {
  id: string;
  case_key: string;
  ordinal: number;
  execution_status: string;
  result_status: string;
  latest_attempt_no: number;
  duration_ms: number | null;
  error_code: string | null;
  error_summary: string | null;
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
  case_keys: string[];
  shard_size?: number;
  idempotency_key?: string;
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

function errorInfo(payload: unknown): { code: string | null; message: string | null } {
  const error = errorObject(payload);
  if (!isRecord(error)) return { code: null, message: null };
  return {
    code: typeof error.code === "string" ? error.code : null,
    message: typeof error.message === "string" ? error.message : null,
  };
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
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
    const info = errorInfo(payload);
    throw new ApiError(info.message ?? `控制面请求失败（HTTP ${response.status}）`, {
      status: response.status,
      code: info.code,
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
    created_at: readOptionalString(payload.created_at),
    started_at: readOptionalString(payload.started_at),
    finished_at: readOptionalString(payload.finished_at),
    idempotency_key: readString(payload.idempotency_key),
    created_by: readString(payload.created_by),
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
    cases: cases.map((entry) => {
      const item = (entry ?? {}) as Record<string, unknown>;
      return {
        id: readString(item.id),
        case_key: readString(item.case_key),
        ordinal: readCount(item.ordinal, "ordinal"),
        execution_status: readString(item.execution_status),
        result_status: readString(item.result_status),
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

export async function fetchEvaluations(signal?: AbortSignal): Promise<EvaluationListResponse> {
  return parseEvaluationList(await request(`/api/v1/evaluations?limit=50`, { signal }));
}

export async function fetchEvaluationDetail(
  runId: string,
  signal?: AbortSignal,
): Promise<EvaluationDetail> {
  return parseEvaluationDetail(await request(`/api/v1/evaluations/${runId}`, { signal }));
}

export async function fetchEvaluationEvents(
  runId: string,
  signal?: AbortSignal,
): Promise<{ items: EvaluationEvent[]; next_cursor: number | null }> {
  const payload = await request(`/api/v1/evaluations/${runId}/events?limit=100`, { signal });
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

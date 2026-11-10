export interface EntityRef {
  slug: string;
  name: string;
}

export const AUTH_UNAUTHORIZED_EVENT = "auth:unauthorized";

/** Let the auth provider react to an expired/revoked session from any request. */
export function notifyUnauthorized(status: number): void {
  if (status === 401 && typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_UNAUTHORIZED_EVENT));
  }
}

export interface OverviewCounts {
  eval_runs: number;
  running: number;
  queued: number;
  failed: number;
  runners_online: number;
  runners_total: number;
}

export interface OverviewResponse {
  tenant: EntityRef;
  project: EntityRef;
  counts: OverviewCounts;
  status: string;
}

export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;
  /** Field-level validation messages from the API envelope, keyed by field. */
  readonly fields: Record<string, string>;
  /** Safe machine-readable diagnostics (``details``); never secret material. */
  readonly details: Record<string, unknown> | null;

  constructor(
    message: string,
    options: {
      status?: number | null;
      code?: string | null;
      requestId?: string | null;
      fields?: Record<string, string>;
      details?: Record<string, unknown> | null;
      cause?: unknown;
    } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
    this.fields = options.fields ?? {};
    this.details = options.details ?? null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readEntity(value: unknown, field: string): EntityRef {
  if (
    !isRecord(value) ||
    typeof value.slug !== "string" ||
    typeof value.name !== "string"
  ) {
    throw new ApiError(`控制面响应缺少 ${field} 字段`, { code: "invalid_response" });
  }
  return { slug: value.slug, name: value.name };
}

function readOptionalEntity(value: unknown, field: string, fallback: EntityRef): EntityRef {
  if (value === null || value === undefined) {
    return fallback;
  }
  return readEntity(value, field);
}

function readCount(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new ApiError(`控制面响应中的 counts.${field} 无效`, {
      code: "invalid_response",
    });
  }
  return value;
}

export function parseOverview(payload: unknown): OverviewResponse {
  if (!isRecord(payload) || !isRecord(payload.counts) || typeof payload.status !== "string") {
    throw new ApiError("控制面返回了无法识别的总览数据", {
      code: "invalid_response",
    });
  }

  return {
    tenant: readEntity(payload.tenant, "tenant"),
    project: readOptionalEntity(payload.project, "project", {
      slug: "default",
      name: "默认项目",
    }),
    status: payload.status,
    counts: {
      eval_runs: readCount(payload.counts.eval_runs, "eval_runs"),
      running: readCount(payload.counts.running, "running"),
      queued: readCount(payload.counts.queued, "queued"),
      failed: readCount(payload.counts.failed, "failed"),
      runners_online: readCount(payload.counts.runners_online, "runners_online"),
      runners_total: readCount(payload.counts.runners_total, "runners_total"),
    },
  };
}

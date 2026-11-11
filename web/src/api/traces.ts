import { ApiError } from "./types";
import { request } from "./evaluations";

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function booleanValue(value: unknown): boolean {
  return value === true;
}

function invalid(field: string): ApiError {
  return new ApiError(`控制面响应中的 ${field} 无效`, { code: "invalid_response" });
}

export interface TraceAttempt {
  id: string;
  attempt_no: number;
  status: string;
  phase: string | null;
  started_at: string | null;
  ended_at: string | null;
  error_code: string | null;
  error_summary: string | null;
}

export interface TraceFile {
  id: string;
  logical_path: string | null;
  artifact_kind: string;
  native_format: string | null;
  format_version: string | null;
  parser_version: string;
  runtime_key: string;
  runtime_version: string | null;
  status: string;
  detected_format: string | null;
  sha256: string;
  size: number;
  error_code: string | null;
  error_summary: string | null;
  line_no: number | null;
}

export interface TraceSession extends Record<string, unknown> {
  id: string;
  parent_session_id: string | null;
  status: string;
  ordinal: number;
  agent_name: string | null;
  agent_id: string | null;
  native_session_id: string;
  runtime_key: string;
  parser_version: string;
  span_count: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cost: string | null;
  relation_evidence: Record<string, unknown>;
  children: TraceSession[];
}

export interface TraceQuality {
  clock_comparable: boolean;
  fileStatuses: Record<string, number>;
  sessionStatuses: Record<string, number>;
  unlinkedSessionIds: string[];
  orphanParentIds: string[];
  issues: Array<{
    trace_file_id: string;
    logical_path: string | null;
    status: string;
    error_code: string | null;
    error_summary: string | null;
    line_no: number | null;
  }>;
}

export interface TraceAttemptResponse {
  caseRunId: string;
  traceStatus: string;
  attempt: TraceAttempt | null;
  files: TraceFile[];
  sessions: TraceSession[];
  stats: {
    sessionCount: number;
    spanCount: number;
    inputTokens: number;
    outputTokens: number;
  };
  quality: TraceQuality;
  parserVersions: string[];
}

export interface TraceSpan {
  id: string;
  session_id: string;
  parent_span_id: string | null;
  native_event_id: string | null;
  ordinal: number;
  kind: string;
  name: string | null;
  status: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  attributes: Record<string, unknown>;
  input_tokens: number | null;
  output_tokens: number | null;
  cost: string | null;
  content_preview: string | null;
  content_available: boolean;
  content_hidden: boolean;
  hidden_reason: string | null;
  parent_missing?: boolean;
}

export interface TraceSpanPage {
  session: {
    id: string;
    agentName: string | null;
    agentId: string | null;
    status: string;
    parserVersion: string;
  };
  items: TraceSpan[];
  pageInfo: {
    limit: number;
    total: number;
    hasMore: boolean;
    nextCursor: string | null;
  };
}

export interface TraceDownloadUrl {
  url: string;
  expiresAt: string;
}

function parseAttempt(value: unknown): TraceAttempt | null {
  if (value === null || value === undefined) return null;
  const item = record(value);
  if (typeof item.id !== "string" || typeof item.attempt_no !== "number") {
    throw invalid("attempt");
  }
  return {
    id: item.id,
    attempt_no: item.attempt_no,
    status: typeof item.status === "string" ? item.status : "unknown",
    phase: stringValue(item.phase),
    started_at: stringValue(item.started_at),
    ended_at: stringValue(item.ended_at),
    error_code: stringValue(item.error_code),
    error_summary: stringValue(item.error_summary),
  };
}

function parseFile(value: unknown): TraceFile {
  const item = record(value);
  if (typeof item.id !== "string") throw invalid("trace file id");
  return {
    id: item.id,
    logical_path: stringValue(item.logical_path),
    artifact_kind: typeof item.artifact_kind === "string" ? item.artifact_kind : "trace",
    native_format: stringValue(item.native_format),
    format_version: stringValue(item.format_version),
    parser_version: typeof item.parser_version === "string" ? item.parser_version : "unknown",
    runtime_key: typeof item.runtime_key === "string" ? item.runtime_key : "unknown",
    runtime_version: stringValue(item.runtime_version),
    status: typeof item.status === "string" ? item.status : "unknown",
    detected_format: stringValue(item.detected_format),
    sha256: typeof item.sha256 === "string" ? item.sha256 : "",
    size: numberValue(item.size) ?? 0,
    error_code: stringValue(item.error_code),
    error_summary: stringValue(item.error_summary),
    line_no: numberValue(item.line_no),
  };
}

function parseSession(value: unknown): TraceSession {
  const item = record(value);
  if (typeof item.id !== "string") throw invalid("trace session id");
  return {
    ...item,
    id: item.id,
    parent_session_id: stringValue(item.parent_session_id),
    status: typeof item.status === "string" ? item.status : "unknown",
    ordinal: numberValue(item.ordinal) ?? 0,
    agent_name: stringValue(item.agent_name),
    agent_id: stringValue(item.agent_id),
    native_session_id:
      typeof item.native_session_id === "string" ? item.native_session_id : item.id,
    runtime_key: typeof item.runtime_key === "string" ? item.runtime_key : "unknown",
    parser_version:
      typeof item.parser_version === "string" ? item.parser_version : "unknown",
    span_count: numberValue(item.span_count) ?? 0,
    input_tokens: numberValue(item.input_tokens),
    output_tokens: numberValue(item.output_tokens),
    cost: stringValue(item.cost),
    relation_evidence: record(item.relation_evidence),
    children: array(item.children).map(parseSession),
  };
}

function parseQuality(value: unknown): TraceQuality {
  const item = record(value);
  const fileQuality = record(item.files);
  const sessionQuality = record(item.sessions);
  return {
    clock_comparable: item.clock_comparable !== false,
    fileStatuses: record(fileQuality.file_statuses) as Record<string, number>,
    sessionStatuses: record(sessionQuality.session_statuses) as Record<string, number>,
    unlinkedSessionIds: array(sessionQuality.unlinked_session_ids).filter(
      (value): value is string => typeof value === "string",
    ),
    orphanParentIds: array(sessionQuality.orphan_parent_ids).filter(
      (value): value is string => typeof value === "string",
    ),
    issues: array(fileQuality.issues).map((entry) => {
      const issue = record(entry);
      return {
        trace_file_id:
          typeof issue.trace_file_id === "string" ? issue.trace_file_id : "",
        logical_path: stringValue(issue.logical_path),
        status: typeof issue.status === "string" ? issue.status : "unknown",
        error_code: stringValue(issue.error_code),
        error_summary: stringValue(issue.error_summary),
        line_no: numberValue(issue.line_no),
      };
    }),
  };
}

export function parseAttemptTrace(payload: unknown): TraceAttemptResponse {
  const item = record(payload);
  if (typeof item.case_run_id !== "string") {
    throw invalid("case_run_id");
  }
  const stats = record(item.stats);
  return {
    caseRunId: item.case_run_id,
    traceStatus: typeof item.trace_status === "string" ? item.trace_status : "pending",
    attempt: parseAttempt(item.attempt),
    files: array(item.files).map(parseFile),
    sessions: array(item.sessions).map(parseSession),
    stats: {
      sessionCount: numberValue(stats.session_count) ?? 0,
      spanCount: numberValue(stats.span_count) ?? 0,
      inputTokens: numberValue(stats.input_tokens) ?? 0,
      outputTokens: numberValue(stats.output_tokens) ?? 0,
    },
    quality: parseQuality(item.quality),
    parserVersions: array(item.parser_versions).filter(
      (value): value is string => typeof value === "string",
    ),
  };
}

function parseSpan(value: unknown): TraceSpan {
  const item = record(value);
  if (typeof item.id !== "string") throw invalid("span id");
  return {
    id: item.id,
    session_id: typeof item.session_id === "string" ? item.session_id : "",
    parent_span_id: stringValue(item.parent_span_id),
    native_event_id: stringValue(item.native_event_id),
    ordinal: numberValue(item.ordinal) ?? 0,
    kind: typeof item.kind === "string" ? item.kind : "MESSAGE",
    name: stringValue(item.name),
    status: stringValue(item.status),
    started_at: stringValue(item.started_at),
    ended_at: stringValue(item.ended_at),
    duration_ms: numberValue(item.duration_ms),
    attributes: record(item.attributes),
    input_tokens: numberValue(item.input_tokens),
    output_tokens: numberValue(item.output_tokens),
    cost: stringValue(item.cost),
    content_preview: stringValue(item.content_preview),
    content_available: booleanValue(item.content_available),
    content_hidden: booleanValue(item.content_hidden),
    hidden_reason: stringValue(item.hidden_reason),
    parent_missing: item.parent_missing === true,
  };
}

export function parseSpanPage(payload: unknown): TraceSpanPage {
  const item = record(payload);
  const session = record(item.session);
  const page = record(item.page_info);
  if (typeof session.id !== "string") throw invalid("span page session");
  return {
    session: {
      id: session.id,
      agentName: stringValue(session.agent_name),
      agentId: stringValue(session.agent_id),
      status: typeof session.status === "string" ? session.status : "unknown",
      parserVersion:
        typeof session.parser_version === "string" ? session.parser_version : "unknown",
    },
    items: array(item.items).map(parseSpan),
    pageInfo: {
      limit: numberValue(page.limit) ?? 50,
      total: numberValue(page.total) ?? 0,
      hasMore: booleanValue(page.has_more),
      nextCursor: stringValue(page.next_cursor),
    },
  };
}

export async function fetchAttemptTrace(
  caseRunId: string,
  attempt: number | null,
  signal?: AbortSignal,
): Promise<TraceAttemptResponse> {
  const params = new URLSearchParams();
  if (attempt !== null) params.set("attempt", String(attempt));
  const suffix = params.size ? `?${params.toString()}` : "";
  return parseAttemptTrace(
    await request(`/api/v1/eval-case-runs/${encodeURIComponent(caseRunId)}/trace${suffix}`, {
      signal,
    }),
  );
}

export async function fetchTraceSessionSpans(
  sessionId: string,
  options: { cursor?: string | null; kind?: string | null; limit?: number } = {},
  signal?: AbortSignal,
): Promise<TraceSpanPage> {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 100));
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.kind) params.set("kind", options.kind);
  return parseSpanPage(
    await request(
      `/api/v1/trace-sessions/${encodeURIComponent(sessionId)}/spans?${params.toString()}`,
      { signal },
    ),
  );
}

export async function fetchTraceSpan(spanId: string, signal?: AbortSignal): Promise<TraceSpan> {
  return parseSpan(
    await request(`/api/v1/trace-spans/${encodeURIComponent(spanId)}`, { signal }),
  );
}

async function parseDownload(
  payload: unknown,
  field: string,
): Promise<TraceDownloadUrl> {
  const item = record(payload);
  if (typeof item.url !== "string" || typeof item.expires_at !== "string") {
    throw invalid(field);
  }
  return { url: item.url, expiresAt: item.expires_at };
}

export async function createTraceFileDownloadUrl(
  fileId: string,
  signal?: AbortSignal,
): Promise<TraceDownloadUrl> {
  return parseDownload(
    await request(`/api/v1/trace-files/${encodeURIComponent(fileId)}/download-url`, {
      method: "POST",
      signal,
    }),
    "trace file download",
  );
}

export async function createTraceSpanContentUrl(
  spanId: string,
  signal?: AbortSignal,
): Promise<TraceDownloadUrl> {
  return parseDownload(
    await request(`/api/v1/trace-spans/${encodeURIComponent(spanId)}/content-url`, {
      method: "POST",
      signal,
    }),
    "trace span content",
  );
}

import { useQuery } from "@tanstack/react-query";
import { ApiError } from "./types";
import { request } from "./evaluations";

export interface ServiceStatusItem {
  key: string;
  name: string;
  status: string;
  summary: string;
  latency_ms: number | null;
}

export interface ResourcePoolStatus {
  id: string;
  name: string;
  provider: string;
  status: string;
  runners_total: number;
  runners_online: number;
}

export interface RunnerStatus {
  id: string;
  name: string;
  provider_runner_id: string;
  pool: string;
  status: string;
  labels: string[];
  last_heartbeat_at: string | null;
}

export interface EvaluationCounts {
  total: number;
  running: number;
  queued: number;
  failed: number;
}

export interface DatasetCounts {
  total: number;
  versions: number;
  cases: number;
}

export interface ModelConfigCounts {
  total: number;
  credentials: number;
  usable: number;
}

export interface OutboxCounts {
  pending: number;
  processing: number;
  failed: number;
}

export interface ResourceStatus {
  pools: ResourcePoolStatus[];
  runners: RunnerStatus[];
  evaluations: EvaluationCounts;
  datasets: DatasetCounts;
  model_configs: ModelConfigCounts;
  outbox: OutboxCounts;
}

export interface DatabaseTableStatus {
  name: string;
  label: string;
  rows: number | null;
}

export interface DatabaseStatus {
  status: string;
  dialect: string | null;
  revision: string | null;
  size_bytes: number | null;
  latency_ms: number | null;
  tables: DatabaseTableStatus[];
}

export interface OperationsStatus {
  generated_at: string;
  environment: string;
  status: string;
  services: ServiceStatusItem[];
  resources: ResourceStatus;
  database: DatabaseStatus;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function number(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function records(value: unknown, field: string): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    throw new ApiError(`控制面响应缺少 ${field} 列表`, { code: "invalid_response" });
  }
  return value.filter(isRecord);
}

function record(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new ApiError(`控制面响应缺少 ${field} 字段`, { code: "invalid_response" });
  }
  return value;
}

function parseService(item: Record<string, unknown>): ServiceStatusItem {
  return {
    key: text(item.key),
    name: text(item.name),
    status: text(item.status) || "unknown",
    summary: text(item.summary),
    latency_ms: nullableNumber(item.latency_ms),
  };
}

function parsePool(item: Record<string, unknown>): ResourcePoolStatus {
  return {
    id: text(item.id),
    name: text(item.name),
    provider: text(item.provider),
    status: text(item.status) || "unknown",
    runners_total: number(item.runners_total),
    runners_online: number(item.runners_online),
  };
}

function parseRunner(item: Record<string, unknown>): RunnerStatus {
  return {
    id: text(item.id),
    name: text(item.name),
    provider_runner_id: text(item.provider_runner_id),
    pool: text(item.pool) || "—",
    status: text(item.status) || "offline",
    labels: Array.isArray(item.labels) ? item.labels.map(String) : [],
    last_heartbeat_at: typeof item.last_heartbeat_at === "string" ? item.last_heartbeat_at : null,
  };
}

function parseResources(value: unknown): ResourceStatus {
  const payload = record(value, "resources");
  const evaluations = record(payload.evaluations, "resources.evaluations");
  const datasets = record(payload.datasets, "resources.datasets");
  const modelConfigs = record(payload.model_configs, "resources.model_configs");
  const outbox = record(payload.outbox, "resources.outbox");
  return {
    pools: records(payload.pools, "resources.pools").map(parsePool),
    runners: records(payload.runners, "resources.runners").map(parseRunner),
    evaluations: {
      total: number(evaluations.total),
      running: number(evaluations.running),
      queued: number(evaluations.queued),
      failed: number(evaluations.failed),
    },
    datasets: {
      total: number(datasets.total),
      versions: number(datasets.versions),
      cases: number(datasets.cases),
    },
    model_configs: {
      total: number(modelConfigs.total),
      credentials: number(modelConfigs.credentials),
      usable: number(modelConfigs.usable),
    },
    outbox: {
      pending: number(outbox.pending),
      processing: number(outbox.processing),
      failed: number(outbox.failed),
    },
  };
}

function parseDatabase(value: unknown): DatabaseStatus {
  const payload = record(value, "database");
  return {
    status: text(payload.status) || "unknown",
    dialect: typeof payload.dialect === "string" ? payload.dialect : null,
    revision: typeof payload.revision === "string" ? payload.revision : null,
    size_bytes: nullableNumber(payload.size_bytes),
    latency_ms: nullableNumber(payload.latency_ms),
    tables: records(payload.tables, "database.tables").map((item) => ({
      name: text(item.name),
      label: text(item.label),
      rows: nullableNumber(item.rows),
    })),
  };
}

export async function fetchOperationsStatus(signal?: AbortSignal): Promise<OperationsStatus> {
  const payload = await request("/api/v1/operations/status", { signal });
  const data = record(payload, "运营状态");
  return {
    generated_at: text(data.generated_at),
    environment: text(data.environment),
    status: text(data.status) || "unknown",
    services: records(data.services, "services").map(parseService),
    resources: parseResources(data.resources),
    database: parseDatabase(data.database),
  };
}

export const operationsQueryKey = ["operations", "status"] as const;

export function useOperationsQuery() {
  return useQuery({
    queryKey: operationsQueryKey,
    queryFn: ({ signal }) => fetchOperationsStatus(signal),
    staleTime: 15_000,
    retry: 1,
    refetchInterval: 30_000,
  });
}

import { useQuery } from "@tanstack/react-query";

import { ApiError } from "./types";
import { requestJson } from "./client";

export type RunnerStatus = "online" | "busy" | "offline";

export interface RunnerItem {
  id: string;
  name: string;
  labels: string[];
  status: RunnerStatus;
  last_heartbeat_at: string | null;
  current_case_attempt_id: string | null;
  resource_pool_id: string;
}

export interface RunnersResponse {
  items: RunnerItem[];
  counts: { online: number; total: number };
  stale_after_seconds: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value) throw new ApiError(`Runner 响应缺少 ${field}`, { code: "invalid_response" });
  return value;
}

function readOptionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function readStatus(value: unknown): RunnerStatus {
  return value === "online" || value === "busy" || value === "offline" ? value : "offline";
}

function parseRunner(value: unknown): RunnerItem {
  if (!isRecord(value)) throw new ApiError("Runner 响应项格式非法", { code: "invalid_response" });
  const labels = Array.isArray(value.labels)
    ? value.labels.filter((label): label is string => typeof label === "string")
    : [];
  return {
    id: readString(value.id, "id"),
    name: readString(value.name, "name"),
    labels,
    status: readStatus(value.status),
    last_heartbeat_at: readOptionalString(value.last_heartbeat_at),
    current_case_attempt_id: readOptionalString(value.current_case_attempt_id),
    resource_pool_id: readString(value.resource_pool_id, "resource_pool_id"),
  };
}

export function parseRunners(payload: unknown): RunnersResponse {
  if (!isRecord(payload) || !Array.isArray(payload.items) || !isRecord(payload.counts)) {
    throw new ApiError("Runner 列表响应格式非法", { code: "invalid_response" });
  }
  return {
    items: payload.items.map(parseRunner),
    counts: {
      online: Number(payload.counts.online ?? 0),
      total: Number(payload.counts.total ?? 0),
    },
    stale_after_seconds: Number(payload.stale_after_seconds ?? 90),
  };
}

export async function fetchRunners(signal?: AbortSignal): Promise<RunnersResponse> {
  const payload = await requestJson("/api/v1/runners", signal);
  return parseRunners(payload);
}

export const runnersQueryKey = ["runners"] as const;

export function useRunnersQuery() {
  return useQuery({
    queryKey: runnersQueryKey,
    queryFn: ({ signal }) => fetchRunners(signal),
    staleTime: 10_000,
    refetchInterval: 15_000,
    retry: 1,
  });
}

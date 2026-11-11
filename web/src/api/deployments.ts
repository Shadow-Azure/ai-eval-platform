import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "./types";
import { request } from "./evaluations";

export interface DeploymentStage {
  key: string;
  label: string;
  status: string;
  detail: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface DeploymentDetail {
  api_image: string;
  web_image: string;
  git_commit: string;
  run_id: string | null;
  run_url: string | null;
}

export interface DeploymentItem {
  id: string;
  environment: string;
  git_commit: string;
  short_commit: string;
  commit_url: string;
  image_digest: string;
  migration_revision: string;
  status: string;
  triggered_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  rollback_of_id: string | null;
  rollback_of_short_commit: string | null;
  is_rollback: boolean;
  run_url: string | null;
  detail: DeploymentDetail;
  stages: DeploymentStage[];
}

export interface HealthComponent {
  key: string;
  name: string;
  status: string;
  summary: string;
}

export interface DeploymentHealth {
  status: string;
  healthy: number;
  total: number;
  components: HealthComponent[];
}

export interface DeploymentDatabase {
  revision: string | null;
  deployment_revision: string | null;
  in_sync: boolean | null;
}

export interface RollbackState {
  available: boolean;
  reason: string;
  message: string;
  database_revision: string | null;
  target: DeploymentItem | null;
}

export interface DeploymentsResponse {
  generated_at: string;
  environment: string;
  github: {
    repository_url: string;
    actions_url: string;
    commit_base_url: string;
  };
  current: DeploymentItem | null;
  previous: DeploymentItem | null;
  latest: DeploymentItem | null;
  database: DeploymentDatabase;
  health: DeploymentHealth;
  rollback: RollbackState;
  items: DeploymentItem[];
  has_more: boolean;
}

export interface RollbackResponse {
  requested: boolean;
  target: DeploymentItem;
  dispatched_at: string | null;
  actions_url: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function record(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new ApiError(`控制面响应缺少 ${field} 字段`, { code: "invalid_response" });
  }
  return value;
}

function records(value: unknown, field: string): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    throw new ApiError(`控制面响应缺少 ${field} 列表`, { code: "invalid_response" });
  }
  return value.filter(isRecord);
}

function parseStage(item: Record<string, unknown>): DeploymentStage {
  return {
    key: text(item.key),
    label: text(item.label),
    status: text(item.status) || "unknown",
    detail: text(item.detail),
    started_at: optionalText(item.started_at),
    finished_at: optionalText(item.finished_at),
    duration_seconds: numberOrNull(item.duration_seconds),
  };
}

function parseDeployment(value: unknown, field: string): DeploymentItem {
  const item = record(value, field);
  const detail = isRecord(item.detail) ? item.detail : {};
  return {
    id: text(item.id),
    environment: text(item.environment),
    git_commit: text(item.git_commit),
    short_commit: text(item.short_commit),
    commit_url: text(item.commit_url),
    image_digest: text(item.image_digest),
    migration_revision: text(item.migration_revision),
    status: text(item.status) || "unknown",
    triggered_by: optionalText(item.triggered_by),
    started_at: optionalText(item.started_at),
    finished_at: optionalText(item.finished_at),
    duration_seconds: numberOrNull(item.duration_seconds),
    rollback_of_id: optionalText(item.rollback_of_id),
    rollback_of_short_commit: optionalText(item.rollback_of_short_commit),
    is_rollback: item.is_rollback === true,
    run_url: optionalText(item.run_url),
    detail: {
      api_image: text(detail.api_image),
      web_image: text(detail.web_image),
      git_commit: text(detail.git_commit),
      run_id: optionalText(detail.run_id),
      run_url: optionalText(detail.run_url),
    },
    stages: records(item.stages, `${field}.stages`).map(parseStage),
  };
}

function optionalDeployment(value: unknown, field: string): DeploymentItem | null {
  return value === null || value === undefined ? null : parseDeployment(value, field);
}

function parseHealth(value: unknown): DeploymentHealth {
  const payload = record(value, "health");
  return {
    status: text(payload.status) || "unknown",
    healthy: numberOrNull(payload.healthy) ?? 0,
    total: numberOrNull(payload.total) ?? 0,
    components: records(payload.components, "health.components").map((item) => ({
      key: text(item.key),
      name: text(item.name),
      status: text(item.status) || "unknown",
      summary: text(item.summary),
    })),
  };
}

function parseRollback(value: unknown): RollbackState {
  const payload = record(value, "rollback");
  return {
    available: payload.available === true,
    reason: text(payload.reason) || "unknown",
    message: text(payload.message),
    database_revision: optionalText(payload.database_revision),
    target: optionalDeployment(payload.target, "rollback.target"),
  };
}

export async function fetchDeployments(signal?: AbortSignal): Promise<DeploymentsResponse> {
  const payload = await request("/api/v1/deployments", { signal });
  const data = record(payload, "交付流水线");
  const github = record(data.github, "github");
  const database = record(data.database, "database");
  return {
    generated_at: text(data.generated_at),
    environment: text(data.environment) || "production",
    github: {
      repository_url: text(github.repository_url),
      actions_url: text(github.actions_url),
      commit_base_url: text(github.commit_base_url),
    },
    current: optionalDeployment(data.current, "current"),
    previous: optionalDeployment(data.previous, "previous"),
    latest: optionalDeployment(data.latest, "latest"),
    database: {
      revision: optionalText(database.revision),
      deployment_revision: optionalText(database.deployment_revision),
      in_sync: typeof database.in_sync === "boolean" ? database.in_sync : null,
    },
    health: parseHealth(data.health),
    rollback: parseRollback(data.rollback),
    items: records(data.items, "items").map((item, index) =>
      parseDeployment(item, `items[${index}]`),
    ),
    has_more: data.has_more === true,
  };
}

export async function requestRollback(
  deploymentId: string,
  reason?: string,
): Promise<RollbackResponse> {
  const payload = await request(
    `/api/v1/deployments/${encodeURIComponent(deploymentId)}/rollback`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ confirm: true, reason: reason?.trim() || undefined }),
    },
  );
  const data = record(payload, "回滚请求");
  return {
    requested: data.requested === true,
    target: parseDeployment(data.target, "rollback.target"),
    dispatched_at: optionalText(data.dispatched_at),
    actions_url: text(data.actions_url),
  };
}

export const deploymentsQueryKey = ["deployments", "pipeline"] as const;

export function useDeploymentsQuery() {
  return useQuery({
    queryKey: deploymentsQueryKey,
    queryFn: ({ signal }) => fetchDeployments(signal),
    staleTime: 15_000,
    retry: 1,
    refetchInterval: 30_000,
  });
}

export function useRollbackMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ deploymentId, reason }: { deploymentId: string; reason?: string }) =>
      requestRollback(deploymentId, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deploymentsQueryKey });
    },
  });
}

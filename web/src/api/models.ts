import { ApiError } from "./types";
import { request } from "./evaluations";

export interface CredentialSummary {
  id: string;
  kind: string;
  provider: string;
  secret_mask: string;
  expires_at: string | null;
  last_rotated_at: string | null;
  revoked_at: string | null;
  created_at: string | null;
}

export interface ModelUsageSummary {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_cny: string;
}

export interface ModelConfig {
  id: string;
  provider: string;
  display_name: string;
  base_url: string;
  model_name: string;
  api_style: string;
  credential_id: string | null;
  limits: Record<string, unknown>;
  pricing: Record<string, unknown>;
  is_temporary: boolean;
  owner_eval_run_id: string | null;
  created_at: string | null;
  credential: CredentialSummary | null;
  last_used_at: string | null;
  usage: ModelUsageSummary;
}

export interface ModelConfigInput {
  provider: string;
  display_name: string;
  base_url: string;
  model_name: string;
  api_style: string;
  pricing?: Record<string, number>;
  limits?: Record<string, unknown>;
  credential_id?: string;
  secret?: string;
}

export type ModelConfigPatch = Partial<Omit<ModelConfigInput, "secret">>;

export interface ConnectivityResult {
  status: string;
  status_code: number;
  model_count: number | null;
}

export const EMPTY_USAGE: ModelUsageSummary = {
  requests: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
  total_tokens: 0,
  cost_cny: "0.000000",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

export function parseCredential(value: unknown): CredentialSummary | null {
  if (!isRecord(value)) return null;
  return {
    id: asString(value.id),
    kind: asString(value.kind),
    provider: asString(value.provider),
    secret_mask: asString(value.secret_mask),
    expires_at: asNullableString(value.expires_at),
    last_rotated_at: asNullableString(value.last_rotated_at),
    revoked_at: asNullableString(value.revoked_at),
    created_at: asNullableString(value.created_at),
  };
}

function parseUsage(value: unknown): ModelUsageSummary {
  if (!isRecord(value)) return { ...EMPTY_USAGE };
  return {
    requests: asNumber(value.requests),
    prompt_tokens: asNumber(value.prompt_tokens),
    completion_tokens: asNumber(value.completion_tokens),
    total_tokens: asNumber(value.total_tokens),
    cost_cny: asString(value.cost_cny, "0.000000"),
  };
}

export function parseModelConfig(value: unknown): ModelConfig {
  if (!isRecord(value) || typeof value.id !== "string") {
    throw new ApiError("控制面返回了无法识别的模型配置", { code: "invalid_response" });
  }
  return {
    id: value.id,
    provider: asString(value.provider),
    display_name: asString(value.display_name),
    base_url: asString(value.base_url),
    model_name: asString(value.model_name),
    api_style: asString(value.api_style, "openai"),
    credential_id: asNullableString(value.credential_id),
    limits: asRecord(value.limits),
    pricing: asRecord(value.pricing),
    is_temporary: value.is_temporary === true,
    owner_eval_run_id: asNullableString(value.owner_eval_run_id),
    created_at: asNullableString(value.created_at),
    credential: parseCredential(value.credential),
    last_used_at: asNullableString(value.last_used_at),
    usage: parseUsage(value.usage),
  };
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export async function fetchModelConfigs(signal?: AbortSignal): Promise<ModelConfig[]> {
  const payload = await request("/api/v1/model-configs", { signal });
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的模型配置列表", {
      code: "invalid_response",
    });
  }
  return payload.items.filter(isRecord).map(parseModelConfig);
}

export async function fetchCredentials(signal?: AbortSignal): Promise<CredentialSummary[]> {
  const payload = await request("/api/v1/credentials?kind=model", { signal });
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new ApiError("控制面返回了无法识别的凭据列表", { code: "invalid_response" });
  }
  return payload.items
    .map(parseCredential)
    .filter((item): item is CredentialSummary => item !== null);
}

export async function createModelConfig(input: ModelConfigInput): Promise<ModelConfig> {
  const payload = await request("/api/v1/model-configs", jsonInit("POST", input));
  return parseModelConfig(payload);
}

export async function updateModelConfig(
  configId: string,
  patch: ModelConfigPatch,
): Promise<ModelConfig> {
  const payload = await request(
    `/api/v1/model-configs/${encodeURIComponent(configId)}`,
    jsonInit("PATCH", patch),
  );
  return parseModelConfig(payload);
}

export async function testModelConfig(configId: string): Promise<ConnectivityResult> {
  const payload = await request(
    `/api/v1/model-configs/${encodeURIComponent(configId)}/test`,
    { method: "POST" },
  );
  if (!isRecord(payload)) {
    throw new ApiError("无法识别连通性测试结果", { code: "invalid_response" });
  }
  return {
    status: asString(payload.status, "unknown"),
    status_code: asNumber(payload.status_code),
    model_count: typeof payload.model_count === "number" ? payload.model_count : null,
  };
}

export async function rotateCredential(
  credentialId: string,
  secret: string,
): Promise<CredentialSummary> {
  const payload = await request(
    `/api/v1/credentials/${encodeURIComponent(credentialId)}/rotate`,
    jsonInit("POST", { secret }),
  );
  const credential = parseCredential(payload);
  if (!credential) {
    throw new ApiError("无法识别轮换后的凭据", { code: "invalid_response" });
  }
  return credential;
}

export async function revokeCredential(credentialId: string): Promise<CredentialSummary> {
  const payload = await request(
    `/api/v1/credentials/${encodeURIComponent(credentialId)}`,
    { method: "DELETE" },
  );
  const credential = parseCredential(payload);
  if (!credential) {
    throw new ApiError("无法识别撤销后的凭据", { code: "invalid_response" });
  }
  return credential;
}

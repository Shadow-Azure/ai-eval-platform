import { request } from "./evaluations";

export interface RuntimeInfo {
  key: string;
  version: string;
  adapter_version: string;
  supported_dataset_adapters: string[];
  required_runner_labels: string[];
  execution_image_digest: string | null;
  scoring_image_digest: string | null;
}

export interface RunnerInfo {
  id: string;
  provider_runner_id: string;
  name: string;
  labels: string[];
  status: string;
  resource_pool_id: string;
}

export async function fetchRuntimes(signal?: AbortSignal): Promise<RuntimeInfo[]> {
  const payload = (await request("/api/v1/runtimes", { signal })) as {
    items?: Array<Record<string, unknown>>;
  };
  return (payload.items ?? []).map((item) => ({
    key: String(item.key ?? ""),
    version: String(item.version ?? ""),
    adapter_version: String(item.adapter_version ?? ""),
    supported_dataset_adapters: Array.isArray(item.supported_dataset_adapters)
      ? item.supported_dataset_adapters.map(String)
      : [],
    required_runner_labels: Array.isArray(item.required_runner_labels)
      ? item.required_runner_labels.map(String)
      : [],
    execution_image_digest:
      typeof item.execution_image_digest === "string" ? item.execution_image_digest : null,
    scoring_image_digest:
      typeof item.scoring_image_digest === "string" ? item.scoring_image_digest : null,
  }));
}

export async function fetchRunners(signal?: AbortSignal): Promise<RunnerInfo[]> {
  const payload = (await request("/api/v1/runners", { signal })) as {
    runners?: Array<Record<string, unknown>>;
  };
  return (payload.runners ?? []).map((item) => ({
    id: String(item.id ?? ""),
    provider_runner_id: String(item.provider_runner_id ?? ""),
    name: String(item.name ?? ""),
    labels: Array.isArray(item.labels) ? item.labels.map(String) : [],
    status: String(item.status ?? "offline"),
    resource_pool_id: String(item.resource_pool_id ?? ""),
  }));
}

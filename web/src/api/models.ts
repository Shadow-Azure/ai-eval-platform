import { request } from "./evaluations";

export interface ModelConfig {
  id: string;
  provider: string;
  display_name: string;
  base_url: string;
  model_name: string;
  api_style: string;
  credential_id: string | null;
}

export async function fetchModelConfigs(signal?: AbortSignal): Promise<ModelConfig[]> {
  const payload = (await request("/api/v1/model-configs", { signal })) as {
    items?: Array<Record<string, unknown>>;
  };
  return (payload.items ?? []).map((item) => ({
    id: String(item.id ?? ""),
    provider: String(item.provider ?? ""),
    display_name: String(item.display_name ?? ""),
    base_url: String(item.base_url ?? ""),
    model_name: String(item.model_name ?? ""),
    api_style: String(item.api_style ?? "openai"),
    credential_id: typeof item.credential_id === "string" ? item.credential_id : null,
  }));
}

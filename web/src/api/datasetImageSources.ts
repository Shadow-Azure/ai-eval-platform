import { request } from "./evaluations";

export interface DatasetImageSource {
  name: string;
  registry: string;
  prefix: string;
  enabled: boolean;
  is_default?: boolean;
}

export interface DatasetImageSourcesView {
  defaults: DatasetImageSource[];
  override: DatasetImageSource[];
  effective: DatasetImageSource[];
  using_override: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseSource(value: unknown): DatasetImageSource {
  if (!isRecord(value)) {
    return { name: "", registry: "", prefix: "", enabled: true };
  }
  return {
    name: typeof value.name === "string" ? value.name : "",
    registry: typeof value.registry === "string" ? value.registry : "",
    prefix: typeof value.prefix === "string" ? value.prefix : "",
    enabled: value.enabled !== false,
    is_default: value.is_default === true,
  };
}

function parseView(payload: unknown): DatasetImageSourcesView {
  const data = isRecord(payload) ? payload : {};
  return {
    defaults: Array.isArray(data.defaults) ? data.defaults.map(parseSource) : [],
    override: Array.isArray(data.override) ? data.override.map(parseSource) : [],
    effective: Array.isArray(data.effective) ? data.effective.map(parseSource) : [],
    using_override: data.using_override === true,
    updated_by: typeof data.updated_by === "string" ? data.updated_by : null,
    updated_at: typeof data.updated_at === "string" ? data.updated_at : null,
  };
}

export async function fetchDatasetImageSources(signal?: AbortSignal): Promise<DatasetImageSourcesView> {
  return parseView(
    await request("/api/v1/settings/dataset-image-sources", { signal }),
  );
}

export async function saveDatasetImageSources(
  sources: DatasetImageSource[],
): Promise<DatasetImageSourcesView> {
  return parseView(
    await request("/api/v1/settings/dataset-image-sources", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sources: sources.map(({ name, registry, prefix, enabled }) => ({
          name,
          registry,
          prefix,
          enabled,
        })),
      }),
    }),
  );
}

export async function resetDatasetImageSources(): Promise<DatasetImageSourcesView> {
  return parseView(
    await request("/api/v1/settings/dataset-image-sources", { method: "DELETE" }),
  );
}

/**
 * Runner-side client for the platform model gateway (M3-02).
 *
 * The runner never receives a provider key. It takes the short-lived model
 * token issued in the shard manifest response headers and forwards it as the
 * bearer credential; the platform gateway verifies it and decrypts the real
 * key. No secret is logged here.
 */

export class ModelGatewayError extends Error {
  constructor(message, { status = 0, code = "model_gateway_error" } = {}) {
    super(message);
    this.name = "ModelGatewayError";
    this.status = status;
    this.code = code;
  }
}

function headerValue(headers, name) {
  if (!headers) return undefined;
  if (typeof headers.get === "function") return headers.get(name) ?? undefined;
  const entry = Object.entries(headers).find(([key]) => key.toLowerCase() === name);
  return entry?.[1];
}

/** Extract the model binding issued by `/runner/v1/shards/{id}/manifest`. */
export function modelConfigFromManifestHeaders(headers) {
  const token = headerValue(headers, "x-runner-model-token");
  const model = headerValue(headers, "x-runner-model-name");
  const openaiBaseUrl = headerValue(headers, "x-runner-model-openai-base-url");
  const anthropicBaseUrl = headerValue(headers, "x-runner-model-anthropic-base-url");
  if (!token || !model) return null;
  return { token, model, openaiBaseUrl, anthropicBaseUrl };
}

function joinUrl(base, path) {
  return `${String(base).replace(/\/+$/, "")}${path}`;
}

async function readError(response) {
  let code = "model_gateway_error";
  let message = `model gateway returned HTTP ${response.status}`;
  try {
    const payload = await response.json();
    const error = payload?.error;
    if (error && typeof error === "object") {
      if (typeof error.code === "string") code = error.code;
      if (typeof error.message === "string") message = error.message;
    }
  } catch {
    // Keep the HTTP status text when the body is not JSON.
  }
  return new ModelGatewayError(message, { status: response.status, code });
}

/** OpenAI-compatible chat completion through the platform gateway. */
export async function createChatCompletion(
  config,
  { messages, model, maxTokens, temperature, fetchImpl = fetch } = {},
) {
  if (!config?.token) throw new ModelGatewayError("缺少模型网关 token", { code: "missing_token" });
  if (!config.openaiBaseUrl) {
    throw new ModelGatewayError("manifest 未提供 OpenAI 兼容网关地址", { code: "missing_base_url" });
  }
  const response = await fetchImpl(joinUrl(config.openaiBaseUrl, "/chat/completions"), {
    method: "POST",
    headers: {
      authorization: `Bearer ${config.token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: model || config.model,
      messages,
      ...(maxTokens === undefined ? {} : { max_tokens: maxTokens }),
      ...(temperature === undefined ? {} : { temperature }),
    }),
  });
  if (!response.ok) throw await readError(response);
  return response.json();
}

/** Anthropic-compatible message request used by Claude Code. */
export async function createAnthropicMessage(
  config,
  { messages, model, maxTokens = 1024, system, fetchImpl = fetch } = {},
) {
  if (!config?.token) throw new ModelGatewayError("缺少模型网关 token", { code: "missing_token" });
  if (!config.anthropicBaseUrl) {
    throw new ModelGatewayError("manifest 未提供 Anthropic 兼容网关地址", { code: "missing_base_url" });
  }
  const response = await fetchImpl(joinUrl(config.anthropicBaseUrl, "/v1/messages"), {
    method: "POST",
    headers: {
      "x-api-key": config.token,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: model || config.model,
      max_tokens: maxTokens,
      messages,
      ...(system === undefined ? {} : { system }),
    }),
  });
  if (!response.ok) throw await readError(response);
  return response.json();
}

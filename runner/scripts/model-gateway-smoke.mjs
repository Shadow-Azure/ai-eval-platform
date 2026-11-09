#!/usr/bin/env node
/**
 * Deployment smoke for the platform model gateway (M3-02).
 *
 * The runner uses a short-lived token issued by the platform; the provider key
 * stays in the control plane. Usage is printed without the token or key.
 *
 *   MODEL_GATEWAY_BASE_URL=http://127.0.0.1:8000/model-gateway/v1 \
 *   MODEL_GATEWAY_ANTHROPIC_BASE_URL=http://127.0.0.1:8000/model-gateway/anthropic \
 *   MODEL_GATEWAY_TOKEN=v1... MODEL_GATEWAY_MODEL=MiniMax-M2.7-highspeed \
 *   node runner/scripts/model-gateway-smoke.mjs --protocol openai
 */
import {
  ModelGatewayError,
  createAnthropicMessage,
  createChatCompletion,
} from "../lib/model-gateway.mjs";

function argument(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

const protocol = argument("protocol", process.env.MODEL_GATEWAY_PROTOCOL || "openai");
const prompt = argument("prompt", "Reply with the single word: pong");
const config = {
  token: process.env.MODEL_GATEWAY_TOKEN,
  model: process.env.MODEL_GATEWAY_MODEL,
  openaiBaseUrl: process.env.MODEL_GATEWAY_BASE_URL,
  anthropicBaseUrl: process.env.MODEL_GATEWAY_ANTHROPIC_BASE_URL,
};

try {
  const result =
    protocol === "anthropic"
      ? await createAnthropicMessage(config, {
          messages: [{ role: "user", content: prompt }],
          maxTokens: 32,
        })
      : await createChatCompletion(config, {
          messages: [{ role: "user", content: prompt }],
          maxTokens: 32,
        });
  const text =
    protocol === "anthropic"
      ? result.content?.map((item) => item.text).filter(Boolean).join("")
      : result.choices?.[0]?.message?.content;
  console.log(JSON.stringify({ ok: true, protocol, model: result.model || config.model, text, usage: result.usage }, null, 2));
} catch (error) {
  if (error instanceof ModelGatewayError) {
    console.error(JSON.stringify({ ok: false, protocol, status: error.status, code: error.code, message: error.message }));
    process.exit(1);
  }
  throw error;
}

import assert from "node:assert/strict";
import test from "node:test";

import {
  ModelGatewayError,
  createAnthropicMessage,
  createChatCompletion,
  modelConfigFromManifestHeaders,
} from "../runner/lib/model-gateway.mjs";

const config = {
  token: "v1.payload.signature",
  model: "MiniMax-M2.7-highspeed",
  openaiBaseUrl: "http://127.0.0.1:8000/model-gateway/v1",
  anthropicBaseUrl: "http://127.0.0.1:8000/model-gateway/anthropic",
};

test("reads the model binding from manifest headers", () => {
  const parsed = modelConfigFromManifestHeaders(
    new Headers({
      "x-runner-model-token": config.token,
      "x-runner-model-name": config.model,
      "x-runner-model-openai-base-url": config.openaiBaseUrl,
      "x-runner-model-anthropic-base-url": config.anthropicBaseUrl,
    }),
  );
  assert.deepEqual(parsed, config);
  assert.equal(modelConfigFromManifestHeaders(new Headers()), null);
});

test("sends an OpenAI-compatible request with the short-lived token", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return new Response(
      JSON.stringify({
        id: "chatcmpl-1",
        choices: [{ message: { role: "assistant", content: "pong" } }],
        usage: { prompt_tokens: 3, completion_tokens: 1 },
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  };
  const payload = await createChatCompletion(config, {
    messages: [{ role: "user", content: "ping" }],
    maxTokens: 8,
    fetchImpl,
  });
  assert.equal(payload.choices[0].message.content, "pong");
  assert.equal(calls[0].url, "http://127.0.0.1:8000/model-gateway/v1/chat/completions");
  assert.equal(calls[0].init.headers.authorization, `Bearer ${config.token}`);
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.model, config.model);
  assert.equal(body.max_tokens, 8);
});

test("sends an Anthropic-compatible request for Claude Code", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ type: "message", content: [{ type: "text", text: "pong" }] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  const payload = await createAnthropicMessage(config, {
    messages: [{ role: "user", content: "ping" }],
    maxTokens: 8,
    fetchImpl,
  });
  assert.equal(payload.type, "message");
  assert.equal(calls[0].url, "http://127.0.0.1:8000/model-gateway/anthropic/v1/messages");
  assert.equal(calls[0].init.headers["x-api-key"], config.token);
  assert.equal(calls[0].init.headers["anthropic-version"], "2023-06-01");
});

test("surfaces the gateway error envelope without leaking the token", async () => {
  const fetchImpl = async () =>
    new Response(
      JSON.stringify({ error: { code: "token_expired", message: "token 已过期", request_id: null } }),
      { status: 401, headers: { "content-type": "application/json" } },
    );
  await assert.rejects(
    createChatCompletion(config, { messages: [], fetchImpl }),
    (error) => {
      assert.ok(error instanceof ModelGatewayError);
      assert.equal(error.status, 401);
      assert.equal(error.code, "token_expired");
      assert.ok(!error.message.includes(config.token));
      return true;
    },
  );
});

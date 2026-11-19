import assert from "node:assert/strict";
import test from "node:test";

import { validate } from "../../runner/lib/json-schema-subset.mjs";

test("oneOf requires exactly one matching branch", () => {
  const schema = { oneOf: [{ const: "a" }, { const: "b" }] };
  assert.deepEqual(validate(schema, "a"), []);
  assert.match(validate(schema, "c").join("; "), /oneOf 必须恰好匹配一个分支/);

  const ambiguous = { oneOf: [{ type: "string" }, { type: "string" }] };
  assert.match(validate(ambiguous, "a").join("; "), /匹配 2 个/);
});

test("oneOf surfaces branch errors", () => {
  const schema = {
    type: "object",
    oneOf: [
      {
        type: "object",
        additionalProperties: false,
        required: ["legacy"],
        properties: { legacy: { type: "string" } },
      },
      {
        type: "object",
        additionalProperties: false,
        required: ["bundle"],
        properties: { bundle: { type: "array" } },
      },
    ],
  };
  const errors = validate(schema, { mixed: true }).join("; ");
  assert.match(errors, /oneOf\[0\]/);
  assert.match(errors, /oneOf\[1\]/);
  assert.match(errors, /legacy/);
  assert.match(errors, /bundle/);
});

test("minProperties and maxProperties bound object keys", () => {
  const schema = {
    type: "object",
    minProperties: 1,
    maxProperties: 2,
    properties: {
      a: { type: "number" },
      b: { type: "number" },
      c: { type: "number" },
    },
  };

  assert.deepEqual(validate(schema, { a: 1 }), []);
  assert.match(validate(schema, {}).join("; "), /至少 1 个属性/);
  assert.match(validate(schema, { a: 1, b: 2, c: 3 }).join("; "), /最多 2 个属性/);
});

test("the dispatch schema caps the top-level payload at ten properties", async () => {
  const { readFileSync } = await import("node:fs");
  const { join } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const here = fileURLToPath(new URL(".", import.meta.url));
  const schema = JSON.parse(
    readFileSync(join(here, "../../runner-contract/schemas/v1/dispatch.schema.json"), "utf8"),
  );
  assert.equal(schema.maxProperties, 10);

  const payload = {
    schema_version: "1.0",
    eval_run_id: "run-1",
    shard_id: "shard_000",
    attempt: 1,
    dispatch_key: "run-1:shard_000:1",
    manifest_url: "http://127.0.0.1:8000/runner/v1/shards/shard_000/manifest?x=1",
    manifest_digest: `sha256:${"a".repeat(64)}`,
    runs_on: ["self-hosted", "linux", "eval"],
    job_timeout_minutes: 70,
    expires_at: "2026-11-19T00:00:00Z",
  };
  assert.deepEqual(validate(schema, payload), []);
  assert.match(validate(schema, { ...payload, extra: true }).join("; "), /最多 10 个属性/);
});

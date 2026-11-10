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

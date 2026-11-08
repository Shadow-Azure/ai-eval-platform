import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { validatePayload } from "../../runner/lib/contracts.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const fixturesDir = join(here, "..", "fixtures", "runner");
const schemaDir = join(here, "..", "..", "runner-contract", "schemas", "v1");
const readFixture = (name) => JSON.parse(readFileSync(join(fixturesDir, name), "utf8"));

test("schema files are valid JSON Schema documents", () => {
  const files = readdirSync(schemaDir).filter((file) => file.endsWith(".schema.json"));
  assert.equal(files.length, 6);
  for (const file of files) {
    const schema = JSON.parse(readFileSync(join(schemaDir, file), "utf8"));
    assert.match(schema.$schema, /2020-12/);
    assert.match(schema.$id, /runner-contract\/v1\//);
    assert.equal(schema.type, "object");
    assert.equal(schema.additionalProperties, false);
    assert.ok(Array.isArray(schema.required) && schema.required.length > 0);
  }
});

const validFixtures = [
  ["dispatch", "dispatch.valid.json"],
  ["manifest", "manifest.valid.json"],
  ["heartbeat", "heartbeat.valid.json"],
  ["upload", "upload.valid.json"],
  ["completion", "completion.valid.json"],
  ["failure", "failure.valid.json"],
];

for (const [kind, file] of validFixtures) {
  test(`${kind} accepts ${file}`, () => {
    const result = validatePayload(kind, readFixture(file));
    assert.equal(result.ok, true, result.errors.join("; "));
  });
}

const invalidFixtures = [
  ["dispatch", "dispatch.invalid-missing-digest.json", /manifest_digest/],
  ["dispatch", "dispatch.invalid-extra-property.json", /model_api_key/],
  ["manifest", "manifest.invalid-attempt.json", /attempt/],
  ["heartbeat", "heartbeat.invalid-sequence.json", /sequence/],
  ["upload", "upload.invalid-sha.json", /sha256/],
  ["completion", "completion.invalid-status.json", /execution_status/],
  ["failure", "failure.invalid-retryable.json", /retryable/],
];

for (const [kind, file, pattern] of invalidFixtures) {
  test(`${kind} rejects ${file}`, () => {
    const result = validatePayload(kind, readFixture(file));
    assert.equal(result.ok, false);
    assert.match(result.errors.join("; "), pattern);
  });
}

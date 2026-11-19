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
  assert.equal(files.length, 7);
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
  ["dispatch", "dispatch.minimal.valid.json"],
  ["manifest", "manifest.valid.json"],
  ["started", "started.valid.json"],
  ["heartbeat", "heartbeat.valid.json"],
  ["upload", "upload.valid.json"],
  ["upload", "upload.multifile.valid.json"],
  ["completion", "completion.valid.json"],
  ["completion", "completion.multitrace.valid.json"],
  ["completion", "completion.trace-legacy.valid.json"],
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
  ["dispatch", "dispatch.minimal.invalid-missing-timeout.json", /job_timeout_minutes/],
  ["manifest", "manifest.invalid-attempt.json", /attempt/],
  ["heartbeat", "heartbeat.invalid-sequence.json", /sequence/],
  ["upload", "upload.invalid-sha.json", /sha256/],
  ["upload", "upload.invalid-logical-path.json", /logical_path/],
  ["upload", "upload.invalid-traversal-path.json", /logical_path/],
  ["completion", "completion.invalid-status.json", /execution_status/],
  ["completion", "completion.invalid-trace-object.json", /logical_path/],
  ["completion", "completion.trace-empty-manifest.invalid.json", /oneOf/],
  ["completion", "completion.trace-legacy-missing-digest.invalid.json", /sha256/],
  ["completion", "completion.trace-mixed-shape.invalid.json", /oneOf/],
  ["completion", "completion.trace-dotdot-path.invalid.json", /logical_path/],
  ["completion", "completion.trace-newline-path.invalid.json", /logical_path/],
  ["failure", "failure.invalid-retryable.json", /retryable/],
];

for (const [kind, file, pattern] of invalidFixtures) {
  test(`${kind} rejects ${file}`, () => {
    const result = validatePayload(kind, readFixture(file));
    assert.equal(result.ok, false);
    assert.match(result.errors.join("; "), pattern);
  });
}

test("dispatch accepts the derived execute job timeout and rejects out-of-range values", () => {
  const dispatch = readFixture("dispatch.valid.json");
  for (const value of [1, 60, 90, 7200]) {
    const result = validatePayload("dispatch", { ...dispatch, job_timeout_minutes: value });
    assert.equal(result.ok, true, `${value}: ${result.errors.join("; ")}`);
  }
  for (const value of [0, 7201, -1, "90"]) {
    const result = validatePayload("dispatch", { ...dispatch, job_timeout_minutes: value });
    assert.equal(result.ok, false);
    assert.match(result.errors.join("; "), /job_timeout_minutes/);
  }
  // Older dispatches stay valid: the workflow falls back to its static floor.
  assert.equal(validatePayload("dispatch", dispatch).ok, true);
});

test("minimal 1.1 dispatch keeps the eight pre-start fields and the legacy shape stays valid", () => {
  const minimal = readFixture("dispatch.minimal.valid.json");
  const legacy = readFixture("dispatch.valid.json");
  assert.equal(validatePayload("dispatch", minimal).ok, true);
  assert.equal(validatePayload("dispatch", legacy).ok, true);
  // A 1.1 dispatch must still freeze the scheduler labels and job timeout.
  const withoutRunsOn = { ...minimal };
  delete withoutRunsOn.runs_on;
  assert.match(validatePayload("dispatch", withoutRunsOn).errors.join("; "), /runs_on/);
  const withoutTimeout = { ...minimal };
  delete withoutTimeout.job_timeout_minutes;
  assert.match(validatePayload("dispatch", withoutTimeout).errors.join("; "), /job_timeout_minutes/);
  // The legacy version still requires the digest and expiry it used to carry.
  const legacyWithoutDigest = { ...legacy };
  delete legacyWithoutDigest.manifest_digest;
  assert.match(validatePayload("dispatch", legacyWithoutDigest).errors.join("; "), /manifest_digest/);
  // 1.1 is exactly the eight pre-start fields: a digest/expiry smuggled back
  // into it is a contract error, not a silently accepted legacy shape.
  const minimalWithDigest = { ...minimal, manifest_digest: legacy.manifest_digest };
  assert.match(validatePayload("dispatch", minimalWithDigest).errors.join("; "), /最多 8 个属性/);
});

test("failure accepts runner cancellation codes and a partial trace manifest", () => {
  const failure = readFixture("failure.valid.json");
  const completion = readFixture("completion.multitrace.valid.json");
  for (const code of ["runner_lost", "execution_timeout"]) {
    const result = validatePayload("failure", { ...failure, error_code: code });
    assert.equal(result.ok, true, `${code}: ${result.errors.join("; ")}`);
  }
  const withTrace = validatePayload("failure", {
    ...failure,
    error_code: "runner_lost",
    trace_manifest: completion.trace_manifest,
  });
  assert.equal(withTrace.ok, true, withTrace.errors.join("; "));
  const invalidTrace = validatePayload("failure", {
    ...failure,
    trace_manifest: { objects: [{ artifact_kind: "score" }] },
  });
  assert.equal(invalidTrace.ok, false);
  assert.match(invalidTrace.errors.join("; "), /trace_manifest/);
});

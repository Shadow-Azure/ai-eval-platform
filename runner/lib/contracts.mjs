import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { validate } from "./json-schema-subset.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const schemaDir = join(here, "..", "..", "runner-contract", "schemas", "v1");

const FILES = {
  dispatch: "dispatch.schema.json",
  manifest: "task-manifest.schema.json",
  heartbeat: "heartbeat.schema.json",
  upload: "upload-manifest.schema.json",
  completion: "completion.schema.json",
  failure: "failure.schema.json",
};

const cache = new Map();

export function schemaFor(kind) {
  const file = FILES[kind];
  if (!file) throw new Error(`未知契约类型：${kind}`);
  if (!cache.has(kind)) {
    cache.set(kind, JSON.parse(readFileSync(join(schemaDir, file), "utf8")));
  }
  return cache.get(kind);
}

export function validatePayload(kind, payload) {
  const errors = validate(schemaFor(kind), payload);
  return { ok: errors.length === 0, errors, kind };
}

export const validateDispatch = (payload) => validatePayload("dispatch", payload);
export const validateManifest = (payload) => validatePayload("manifest", payload);
export const validateHeartbeat = (payload) => validatePayload("heartbeat", payload);
export const validateUpload = (payload) => validatePayload("upload", payload);
export const validateCompletion = (payload) => validatePayload("completion", payload);
export const validateFailure = (payload) => validatePayload("failure", payload);

export function requireValid(kind, payload) {
  const result = validatePayload(kind, payload);
  if (!result.ok) {
    const error = new Error(`${kind} 校验失败：${result.errors.join("; ")}`);
    error.code = "contract_invalid";
    error.errors = result.errors;
    throw error;
  }
  return payload;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [kind, file] = process.argv.slice(2);
  if (!kind) {
    console.error("用法：node runner/lib/contracts.mjs <dispatch|manifest|heartbeat|upload|completion|failure> <file>");
    process.exit(2);
  }
  const text = !file || file === "-" ? readFileSync(0, "utf8") : readFileSync(file, "utf8");
  try {
    const payload = JSON.parse(text);
    const result = validatePayload(kind, payload);
    console.log(JSON.stringify(result, null, 2));
    process.exit(result.ok ? 0 : 1);
  } catch (error) {
    console.error(JSON.stringify({ ok: false, errors: [String(error.message ?? error)] }, null, 2));
    process.exit(1);
  }
}

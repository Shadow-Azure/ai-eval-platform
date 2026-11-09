#!/usr/bin/env node
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { validateDispatch, validateManifest } from "../lib/contracts.mjs";
import { fetchManifest, sha256 } from "../lib/manifest-source.mjs";

/**
 * M4-01 prepare entry: turn a shard dispatch into a GitHub matrix where every
 * job executes exactly one case.
 *
 * It only reads the dispatch and the manifest: it never claims cases, never
 * writes to the platform and never carries case data into the workflow. Local
 * claims are per-job state; the platform `started`/`completed` idempotency is
 * the cross-machine authority.
 */

function parseArgs(argv) {
  const args = { dispatch: null, stateDir: ".runner-state", out: null, help: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (arg === "--dispatch") { args.dispatch = next; index += 1; }
    else if (arg === "--state-dir") { args.stateDir = next; index += 1; }
    else if (arg === "--out") { args.out = next; index += 1; }
    else if (arg === "--help") { args.help = true; }
    else throw new Error(`未知参数：${arg}`);
  }
  return args;
}

function fail(message, code, exitCode = 2) {
  console.error(JSON.stringify({ status: "rejected", error_code: code, message }, null, 2));
  process.exit(exitCode);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.dispatch) {
    console.log("用法：node runner/scripts/plan-cases.mjs --dispatch <file> --state-dir <dir> [--out <file>]");
    return 0;
  }
  // --state-dir is accepted for symmetry with real-task.mjs; prepare holds no
  // claims (each execute job owns its own claim), so it is never touched here.

  let dispatch;
  try {
    dispatch = JSON.parse(readFileSync(resolve(args.dispatch), "utf8"));
  } catch (error) {
    fail(`dispatch 读取失败：${error.message}`, "manifest_invalid");
  }

  const dispatchCheck = validateDispatch(dispatch);
  if (!dispatchCheck.ok) fail(`dispatch 校验失败：${dispatchCheck.errors.join("; ")}`, "manifest_invalid");

  if (Date.parse(dispatch.expires_at) < Date.now()) fail("dispatch 已过期", "token_invalid");

  const { bytes } = await fetchManifest(dispatch.manifest_url).catch((error) =>
    fail(error.message, "manifest_invalid")
  );
  if (sha256(bytes) !== dispatch.manifest_digest) fail("manifest digest 不匹配", "manifest_digest_mismatch");

  let manifest;
  try {
    manifest = JSON.parse(bytes.toString("utf8"));
  } catch (error) {
    fail(`manifest 不是 JSON：${error.message}`, "manifest_invalid");
  }
  const manifestCheck = validateManifest(manifest);
  if (!manifestCheck.ok) fail(`manifest 校验失败：${manifestCheck.errors.join("; ")}`, "manifest_invalid");

  for (const key of ["eval_run_id", "shard_id", "attempt", "dispatch_key"]) {
    if (manifest[key] !== dispatch[key]) fail(`manifest.${key} 与 dispatch 不一致`, "attempt_mismatch");
  }

  // Single-line JSON array in manifest order; only the opaque case id reaches
  // the matrix, never prompts, assets or credentials.
  const matrix = JSON.stringify(manifest.cases.map((item) => ({ id: item.case_run_id })));
  if (args.out) {
    writeFileSync(resolve(args.out), `${matrix}\n`);
  } else {
    process.stdout.write(`${matrix}\n`);
  }
  return 0;
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error(JSON.stringify({ status: "rejected", error_code: "runtime_error", message: String(error.message ?? error) }, null, 2));
  process.exit(2);
});

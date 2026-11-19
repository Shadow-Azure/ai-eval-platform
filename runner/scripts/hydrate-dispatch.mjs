#!/usr/bin/env node
/**
 * M8-51 (#323) Plan C: hydrate an 8-field dispatch with the shard descriptor.
 *
 * The repository_dispatch client_payload keeps only the fields the workflow
 * needs before the job starts. The manifest digest and expiry are fetched from
 * the shard descriptor endpoint (authenticated with the short-lived task token
 * the manifest endpoint issues) and merged into the runner-facing dispatch
 * file. The scripts bundle pin is served by the existing /scripts endpoint and
 * stays out of this file to keep it at 10 properties. Version 1.0 dispatches
 * already carry the fields and are left untouched.
 *
 * Usage:
 *   node runner/scripts/hydrate-dispatch.mjs --dispatch <file> [--out <file>]
 */

import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { validateDispatch } from "../lib/contracts.mjs";
import { fetchManifest, sha256 } from "../lib/manifest-source.mjs";

const BUNDLE_NAME = /^eval-runner-scripts-[0-9a-f]{32}\.tar\.gz$/;

function parseArgs(argv) {
  const args = { dispatch: null, out: null, timeoutMs: 10000 };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (arg === "--dispatch") { args.dispatch = next; index += 1; }
    else if (arg === "--out") { args.out = next; index += 1; }
    else if (arg === "--timeout-ms") { args.timeoutMs = Number(next); index += 1; }
    else if (arg === "--help") { args.help = true; }
    else throw new Error(`未知参数：${arg}`);
  }
  return args;
}

function fail(message, code = "descriptor_unavailable", exitCode = 2) {
  console.error(JSON.stringify({ status: "rejected", error_code: code, message }, null, 2));
  process.exit(exitCode);
}

function descriptorUrl(manifestUrl) {
  const url = new URL(manifestUrl);
  if (!/\/manifest$/.test(url.pathname)) {
    throw new Error("manifest_url 不以 /manifest 结尾，无法推导 descriptor");
  }
  url.pathname = url.pathname.replace(/\/manifest$/, "/descriptor");
  return url.toString();
}

function validBundle(pin) {
  return (
    pin &&
    typeof pin === "object" &&
    typeof pin.name === "string" &&
    BUNDLE_NAME.test(pin.name) &&
    typeof pin.md5 === "string" &&
    /^[0-9a-f]{32}$/.test(pin.md5) &&
    typeof pin.sha256 === "string" &&
    /^[0-9a-f]{64}$/.test(pin.sha256) &&
    Number.isInteger(pin.size) &&
    pin.size > 0
  );
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.dispatch) {
    console.log("用法：node runner/scripts/hydrate-dispatch.mjs --dispatch <file> [--out <file>]");
    return 0;
  }

  const path = resolve(args.dispatch);
  let dispatch;
  try {
    dispatch = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    fail(`dispatch 读取失败：${error.message}`, "dispatch_invalid");
  }

  // Legacy 1.0 dispatches already carry the descriptor fields.
  if (dispatch.schema_version === "1.0") {
    const out = args.out ? resolve(args.out) : null;
    if (out) writeFileSync(out, `${JSON.stringify(dispatch, null, 2)}\n`);
    else process.stdout.write(`${JSON.stringify(dispatch, null, 2)}\n`);
    return 0;
  }

  if (dispatch.schema_version !== "1.1") {
    fail(`未知 dispatch schema_version：${dispatch.schema_version}`, "dispatch_invalid");
  }

  const timeout = AbortSignal.timeout(Math.max(1000, args.timeoutMs));
  let manifest;
  try {
    manifest = await fetchManifest(dispatch.manifest_url);
  } catch (error) {
    fail(`manifest 拉取失败：${error.message}`, "descriptor_unavailable");
  }
  const computedDigest = sha256(manifest.bytes);
  if (!manifest.tokenHeader) fail("manifest 响应缺少 X-Runner-Task-Token", "descriptor_token_missing");

  let descriptor;
  try {
    const response = await fetch(descriptorUrl(dispatch.manifest_url), {
      headers: { authorization: `Bearer ${manifest.tokenHeader}` },
      signal: timeout,
    });
    if (!response.ok) fail(`descriptor 拉取失败：HTTP ${response.status}`, "descriptor_unavailable");
    descriptor = await response.json();
  } catch (error) {
    fail(`descriptor 拉取失败：${error.message}`, "descriptor_unavailable");
  }

  if (descriptor.schema_version !== "1.0") fail("descriptor schema_version 非法", "descriptor_invalid");
  if (descriptor.manifest_digest !== computedDigest) {
    fail("descriptor digest 与 manifest 字节不一致", "manifest_digest_mismatch");
  }
  if (typeof descriptor.expires_at !== "string" || Number.isNaN(Date.parse(descriptor.expires_at))) {
    fail("descriptor expires_at 非法", "descriptor_invalid");
  }
  if (descriptor.scripts_bundle != null && !validBundle(descriptor.scripts_bundle)) {
    fail("descriptor scripts_bundle 非法", "descriptor_invalid");
  }

  const hydrated = {
    ...dispatch,
    manifest_digest: descriptor.manifest_digest,
    expires_at: descriptor.expires_at,
  };

  const check = validateDispatch(hydrated);
  if (!check.ok) fail(`hydrated dispatch 校验失败：${check.errors.join("; ")}`, "dispatch_invalid");

  const out = args.out ? resolve(args.out) : null;
  const text = `${JSON.stringify(hydrated, null, 2)}\n`;
  if (out) writeFileSync(out, text);
  else process.stdout.write(text);
  return 0;
}

main().then((code) => process.exit(code)).catch((error) => {
  fail(String(error?.message ?? error), "hydrate_failed");
});

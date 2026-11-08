#!/usr/bin/env node
// Generate a self-contained synthetic dispatch for M2-04 smoke runs.
//
// The output pair (dispatch.json + manifest.json) satisfies
// runner-contract/schemas/v1, with manifest_url pointing at a local file://
// URI so a workflow step needs no HTTP manifest server. The short-lived task
// token is written to task-token (0600, runner temp dir only) and never
// printed. M2-05 replaces this bootstrap with platform-issued dispatches and
// OIDC/short-term tokens.
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync, chmodSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createToken } from "../runner/lib/auth.mjs";
import { validateDispatch, validateManifest } from "../runner/lib/contracts.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function parseArgs(argv) {
  const args = {
    outDir: null,
    callbackBaseUrl: "http://127.0.0.1:8000",
    evalRunId: "eval_m204_smoke",
    shardId: "shard_m204",
    attempt: 1,
    caseRunId: "ecr_m204",
    caseKey: "case-m204",
    ttlSeconds: 3600,
    deadlineMinutes: 30,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = argv[i + 1];
    if (arg === "--out-dir") { args.outDir = next; i += 1; }
    else if (arg === "--callback-base-url") { args.callbackBaseUrl = next; i += 1; }
    else if (arg === "--eval-run-id") { args.evalRunId = next; i += 1; }
    else if (arg === "--shard-id") { args.shardId = next; i += 1; }
    else if (arg === "--case-run-id") { args.caseRunId = next; i += 1; }
    else { throw new Error(`未知参数：${arg}`); }
  }
  if (!args.outDir) throw new Error("--out-dir 必填");
  return args;
}

const args = parseArgs(process.argv.slice(2));
const outDir = resolve(args.outDir);
mkdirSync(outDir, { recursive: true });

const now = Date.now();
const dispatchKey = `${args.evalRunId}:${args.shardId}:${args.attempt}`;
const manifestPath = join(outDir, "manifest.json");

const manifest = {
  schema_version: "1.0",
  eval_run_id: args.evalRunId,
  shard_id: args.shardId,
  attempt: args.attempt,
  dispatch_key: dispatchKey,
  callback_base_url: args.callbackBaseUrl,
  deadline: new Date(now + args.deadlineMinutes * 60_000).toISOString(),
  case_timeout_seconds: 900,
  max_attempts: 2,
  cases: [{ case_run_id: args.caseRunId, case_key: args.caseKey }],
};
const manifestText = `${JSON.stringify(manifest, null, 2)}\n`;
writeFileSync(manifestPath, manifestText);

const dispatch = {
  schema_version: "1.0",
  eval_run_id: args.evalRunId,
  shard_id: args.shardId,
  attempt: args.attempt,
  dispatch_key: dispatchKey,
  manifest_url: `file://${manifestPath}`,
  manifest_digest: `sha256:${createHash("sha256").update(manifestText).digest("hex")}`,
  expires_at: new Date(now + args.ttlSeconds * 1000).toISOString(),
};

for (const [kind, payload] of [["dispatch", dispatch], ["manifest", manifest]]) {
  const check = kind === "dispatch" ? validateDispatch(payload) : validateManifest(payload);
  if (!check.ok) {
    console.error(`${kind} 校验失败：${check.errors.join("; ")}`);
    process.exit(1);
  }
}

writeFileSync(join(outDir, "dispatch.json"), `${JSON.stringify(dispatch, null, 2)}\n`);

const secret = process.env.RUNNER_TASK_SECRET ?? "development-only-secret";
const token = createToken(
  { eval_run_id: args.evalRunId, shard_id: args.shardId, attempt: args.attempt },
  secret,
  { ttlSeconds: args.ttlSeconds, now },
);
const tokenPath = join(outDir, "task-token");
writeFileSync(tokenPath, `${token}\n`);
chmodSync(tokenPath, 0o600);

const summary = {
  dispatch_key: dispatchKey,
  eval_run_id: args.evalRunId,
  shard_id: args.shardId,
  attempt: args.attempt,
  cases: [args.caseRunId],
  callback_base_url: args.callbackBaseUrl,
  manifest_digest: dispatch.manifest_digest,
  expires_at: dispatch.expires_at,
  token_path: tokenPath,
};
writeFileSync(join(outDir, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
console.log(JSON.stringify(summary, null, 2));

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { createToken } from "../../runner/lib/auth.mjs";
import { startMockPlatform } from "../../runner/lib/mock-platform.mjs";

/**
 * Shared scenario fixtures for the M4-01 prepare/execute scripts. The helpers
 * are deliberately dependency-free: a mock platform over loopback, a dispatch
 * file in a scratch dir and a child-process runner.
 */

export const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const taskSecret = "test-secret";
export const future = (offsetMs = 3_600_000) => new Date(Date.now() + offsetMs).toISOString();
export const sha256 = (text) => `sha256:${createHash("sha256").update(text).digest("hex")}`;

export function runNode(scriptPath, args, env = {}) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [scriptPath, ...args], {
      cwd: repoRoot,
      env: { ...process.env, ...env },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

export async function createScenario({
  cases = [
    { case_run_id: "ecr_a", case_key: "case-a", dataset_case_id: "case-a" },
    { case_run_id: "ecr_b", case_key: "case-b", dataset_case_id: "case-b" },
  ],
  behavior = {},
  dispatchOverrides = {},
  manifestOverrides = {},
  tokenTtlSeconds = 600,
  expiresInMs = 3_600_000,
  manifestDigest,
  dispatchVersion = "1.1",
} = {}) {
  const evalRunId = dispatchOverrides.eval_run_id ?? "eval_test";
  const shardId = dispatchOverrides.shard_id ?? "shard_test";
  const attempt = dispatchOverrides.attempt ?? 1;
  const token = createToken(
    { eval_run_id: evalRunId, shard_id: shardId, attempt, pool_id: "pool_test" },
    taskSecret,
    { ttlSeconds: tokenTtlSeconds },
  );
  let manifestText = "";
  const platform = await startMockPlatform({
    manifestText: () => manifestText,
    expectedToken: token,
    behavior,
    manifestExpiresAt: () => future(expiresInMs),
  });
  const manifest = {
    schema_version: "1.0",
    eval_run_id: evalRunId,
    shard_id: shardId,
    attempt,
    dispatch_key: `${evalRunId}:${shardId}:${attempt}`,
    callback_base_url: platform.baseUrl,
    deadline: future(),
    case_timeout_seconds: 300,
    max_attempts: 2,
    cases,
    ...manifestOverrides,
  };
  manifestText = `${JSON.stringify(manifest, null, 2)}\n`;
  const coreDispatch = {
    eval_run_id: evalRunId,
    shard_id: shardId,
    attempt,
    dispatch_key: manifest.dispatch_key,
    manifest_url: `${platform.baseUrl}/manifest`,
  };
  // M8-51 (#323) option C: the default fixture is the minimal 1.1 dispatch the
  // platform now sends (digest/expiry come from the manifest response). Pass
  // dispatchVersion: "1.0" to exercise the legacy payload.
  const dispatch =
    dispatchVersion === "1.1"
      ? {
          schema_version: "1.1",
          ...coreDispatch,
          runs_on: ["self-hosted", "linux", "eval"],
          job_timeout_minutes: 70,
          ...(manifestDigest ? { manifest_digest: manifestDigest } : {}),
          ...dispatchOverrides,
        }
      : {
          schema_version: "1.0",
          ...coreDispatch,
          manifest_digest: manifestDigest ?? sha256(manifestText),
          expires_at: future(expiresInMs),
          ...dispatchOverrides,
        };
  const dir = mkdtempSync(join(tmpdir(), "m401-"));
  const dispatchPath = join(dir, "dispatch.json");
  writeFileSync(dispatchPath, `${JSON.stringify(dispatch, null, 2)}\n`);
  return {
    platform,
    manifest,
    dispatch,
    dir,
    dispatchPath,
    stateDir: join(dir, "state"),
    objectRoot: join(dir, "objects"),
    token,
    close: () => platform.close(),
  };
}

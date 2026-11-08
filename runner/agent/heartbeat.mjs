#!/usr/bin/env node
import process from "node:process";

export function parseLabels(value) {
  return (value || "self-hosted,linux,eval")
    .split(",")
    .map((label) => label.trim())
    .filter(Boolean);
}

export function buildPayload(env) {
  const attempt = env.RUNNER_CURRENT_CASE_ATTEMPT_ID || null;
  return {
    runner_id: env.RUNNER_ID || env.RUNNER_NAME || "fixed-linux-runner",
    name: env.RUNNER_NAME || env.RUNNER_ID || "fixed-linux-runner",
    labels: parseLabels(env.RUNNER_LABELS),
    status: attempt ? "busy" : "online",
    current_case_attempt_id: attempt,
  };
}

export async function sendHeartbeat(env, fetchImpl = fetch) {
  const base = (env.PLATFORM_BASE_URL || "").replace(/\/$/, "");
  if (!base) throw new Error("PLATFORM_BASE_URL 未配置");
  if (!env.RUNNER_HEARTBEAT_TOKEN) throw new Error("RUNNER_HEARTBEAT_TOKEN 未配置");
  const response = await fetchImpl(`${base}/runner/v1/runners/heartbeat`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${env.RUNNER_HEARTBEAT_TOKEN}`,
    },
    body: JSON.stringify(buildPayload(env)),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) {
    throw new Error(`heartbeat failed: HTTP ${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function main() {
  const interval = Math.max(5, Number(process.env.HEARTBEAT_INTERVAL_SECONDS || 30));
  let stopped = false;
  process.on("SIGTERM", () => { stopped = true; });
  process.on("SIGINT", () => { stopped = true; });
  while (!stopped) {
    try {
      const result = await sendHeartbeat(process.env);
      console.log(JSON.stringify({ event: "heartbeat", status: result.status ?? "ok", at: new Date().toISOString() }));
    } catch (error) {
      console.error(`heartbeat: ${error.message ?? error}`);
    }
    await new Promise((resolve) => setTimeout(resolve, interval * 1000));
  }
}

if (import.meta.url === `file://${process.argv[1]}`) main();

#!/usr/bin/env node
/**
 * Run one Claude Code session through the platform model gateway (M3-03).
 *
 * Expected environment:
 *   CLAUDE_GATEWAY_BASE_URL, CLAUDE_GATEWAY_TOKEN, CLAUDE_MODEL,
 *   WORKSPACE (default .), RUNTIME_HOME (default <workspace>/.runtime/claude-home),
 *   PROMPT (default a deterministic smoke prompt), MAX_TURNS (default 8).
 */
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { runClaudeCode } from "../lib/runtime-claude-code.mjs";

const workspace = resolve(process.env.WORKSPACE || ".");
const runtimeHome = resolve(process.env.RUNTIME_HOME || `${workspace}/.runtime/claude-home`);
mkdirSync(runtimeHome, { recursive: true });

try {
  const result = await runClaudeCode({
    prompt: process.env.PROMPT || "Reply with the single word: pong. Do not modify files.",
    model: process.env.CLAUDE_MODEL,
    workspace,
    runtimeHome,
    gatewayBaseUrl: process.env.CLAUDE_GATEWAY_BASE_URL,
    gatewayToken: process.env.CLAUDE_GATEWAY_TOKEN,
    maxTurns: Number(process.env.MAX_TURNS || 8),
    executionImageDigest: process.env.EXECUTION_IMAGE_DIGEST || null,
    timeoutMs: Number(process.env.TIMEOUT_MS || 600_000),
  });
  console.log(
    JSON.stringify(
      {
        ok: result.exitCode === 0,
        exit_code: result.exitCode,
        events: result.events.map((event) => event.type),
        trace_files: result.traces,
        stdout_path: result.stdoutPath,
        stderr_path: result.stderrPath,
      },
      null,
      2,
    ),
  );
  process.exit(result.exitCode === 0 ? 0 : 1);
} catch (error) {
  console.error(JSON.stringify({ ok: false, code: error.code, message: error.message }));
  process.exit(1);
}

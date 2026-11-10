/**
 * Claude Code runtime adapter for the in-job runner (M3-03).
 *
 * The platform validates and freezes the asset snapshot; this module applies
 * exactly those files under an isolated runtime HOME, launches the reviewed
 * `claude` command with structured argv (never a shell), streams the native
 * JSONL output, and collects the raw session files. Trace parsing and
 * parent/child relations belong to M6.
 */
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { readdir, stat } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";

export const RUNTIME_KEY = "claude-code";
export const ADAPTER_VERSION = "claude-code-1.0";
export const ALLOWED_ASSET_ROOTS = ["skills", "commands", "agents"];
const MAX_ASSETS = 256;
const MAX_TOTAL_BYTES = 8 * 1024 * 1024;
const MAX_EVENT_TEXT = 4000;

export class RuntimeError extends Error {
  constructor(message, { code = "runtime_error" } = {}) {
    super(message);
    this.name = "RuntimeError";
    this.code = code;
  }
}

function safeRelative(path) {
  if (typeof path !== "string" || !path || path.includes("\0") || path.includes("\\")) return null;
  if (path.startsWith("/")) return null;
  const parts = path.split("/");
  if (parts.some((part) => part === "" || part === "." || part === "..")) return null;
  return parts;
}

/** Validate an asset snapshot; returns an array of {code, message, path}. */
export function validateAssets(files) {
  const issues = [];
  const entries = Object.entries(files || {});
  if (entries.length > MAX_ASSETS) {
    issues.push({ code: "asset_count_exceeded", message: `超过 ${MAX_ASSETS} 个资产` });
  }
  let total = 0;
  const skillRoots = new Set();
  for (const [path, content] of entries) {
    const parts = safeRelative(path);
    if (!parts) {
      issues.push({ code: "asset_path_invalid", message: "资产路径必须是安全的相对路径", path });
      continue;
    }
    if (!ALLOWED_ASSET_ROOTS.includes(parts[0])) {
      issues.push({ code: "asset_path_not_allowed", message: "只允许 skills/commands/agents/", path });
      continue;
    }
    const bytes = Buffer.isBuffer(content) ? content : Buffer.from(String(content));
    total += bytes.length;
    if (parts[0] === "skills") {
      if (parts.length < 2) {
        issues.push({ code: "skill_root_invalid", message: "Skill 必须以目录为单位", path });
        continue;
      }
      skillRoots.add(parts[1]);
      if (bytes.length === 0) issues.push({ code: "asset_empty", message: "资产文件不能为空", path });
    } else {
      if (parts.length < 2 || !parts.at(-1).endsWith(".md")) {
        issues.push({ code: "asset_extension_invalid", message: `${parts[0]} 资产必须是 .md`, path });
      }
      if (bytes.length === 0) issues.push({ code: "asset_empty", message: "资产文件不能为空", path });
    }
  }
  if (total > MAX_TOTAL_BYTES) {
    issues.push({ code: "asset_size_exceeded", message: `资产总大小超过 ${MAX_TOTAL_BYTES}` });
  }
  for (const root of skillRoots) {
    if (!(`skills/${root}/SKILL.md` in (files || {}))) {
      issues.push({
        code: "skill_manifest_missing",
        message: "每个 Skill 目录必须包含 SKILL.md",
        path: `skills/${root}/SKILL.md`,
      });
    }
  }
  return issues;
}

/** Apply only the frozen snapshot under <workspace>/.claude. */
export function materializeAssets(files, workspace) {
  const issues = validateAssets(files);
  if (issues.length > 0) {
    throw new RuntimeError(`资产校验失败：${issues.map((issue) => issue.message).join("; ")}`, {
      code: "asset_invalid",
    });
  }
  const root = resolve(workspace, ".claude");
  const written = [];
  for (const [path, content] of Object.entries(files || {})) {
    const parts = safeRelative(path);
    if (!parts) continue;
    const destination = join(root, ...parts);
    const relativeToRoot = relative(root, destination);
    if (relativeToRoot.startsWith("..") || relativeToRoot.includes(`..${sep}`)) {
      throw new RuntimeError("资产路径越界", { code: "asset_path_invalid" });
    }
    mkdirSync(dirname(destination), { recursive: true });
    writeFileSync(destination, Buffer.isBuffer(content) ? content : String(content));
    written.push(join(".claude", ...parts));
  }
  return written.sort();
}

function childEnv({ runtimeHome, gatewayBaseUrl, gatewayToken, model, baseEnv = process.env }) {
  const env = { ...baseEnv };
  for (const key of ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"]) {
    delete env[key];
  }
  return {
    ...env,
    HOME: runtimeHome,
    CLAUDE_CONFIG_DIR: join(runtimeHome, ".claude"),
    ANTHROPIC_BASE_URL: String(gatewayBaseUrl).replace(/\/+$/, ""),
    ANTHROPIC_AUTH_TOKEN: gatewayToken,
    ANTHROPIC_MODEL: model,
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1",
    DISABLE_TELEMETRY: "1",
  };
}

/** Build the reviewed launch spec. argv is passed to spawn without a shell. */
export function buildClaudeCommand({
  prompt,
  model,
  workspace,
  runtimeHome,
  gatewayBaseUrl,
  gatewayToken,
  maxTurns = 40,
  executionImageDigest = null,
  command = "claude",
}) {
  if (!prompt || !String(prompt).trim()) throw new RuntimeError("prompt 不能为空");
  if (!model) throw new RuntimeError("model 不能为空");
  if (!gatewayToken) throw new RuntimeError("缺少模型网关 token", { code: "missing_token" });
  if (!/^https?:\/\//.test(String(gatewayBaseUrl || ""))) {
    throw new RuntimeError("模型网关 base URL 无效", { code: "invalid_base_url" });
  }
  // Claude Code validates the model against its own allowlist. Custom
  // providers are selected by the gateway token/config; send a recognized
  // alias and let the gateway rewrite it to the configured upstream model.
  const cliModel = String(model).startsWith("claude-") ? String(model) : "claude-sonnet-4-20250514";
  return {
    command,
    args: [
      "-p",
      String(prompt),
      "--output-format",
      "stream-json",
      "--verbose",
      "--model",
      cliModel,
      "--max-turns",
      String(Math.max(1, Math.trunc(maxTurns))),
      "--permission-mode",
      "acceptEdits",
      // Disposable non-root sandbox: the reviewed template needs Bash/Edit
      // tools without an interactive approval prompt.
      "--dangerously-skip-permissions",
    ],
    cwd: workspace,
    env: childEnv({ runtimeHome, gatewayBaseUrl, gatewayToken, model: cliModel }),
    executionImageDigest,
  };
}

function trimmed(value) {
  if (typeof value !== "string") return null;
  return value.slice(0, MAX_EVENT_TEXT);
}

/** Map Claude Code JSONL lines to minimal live events (M3 shared contract). */
export function consumeStreamLines(lines) {
  const events = [];
  let sessionId = null;
  for (const raw of lines) {
    const text = String(raw).trim();
    if (!text) continue;
    let item;
    try {
      item = JSON.parse(text);
    } catch {
      continue;
    }
    if (!item || typeof item !== "object") continue;
    if (item.type === "system") {
      sessionId = item.session_id || item.sessionId || sessionId;
      events.push({ type: "session_started", session_id: sessionId, payload: { model: item.model } });
    } else if (item.type === "assistant") {
      const content = Array.isArray(item.message?.content) ? item.message.content : [];
      const textParts = content.filter((part) => part?.type === "text").map((part) => part.text || "");
      const tool = content.find((part) => part?.type === "tool_use");
      events.push({
        type: "assistant_message",
        session_id: sessionId,
        text: trimmed(textParts.join("")),
        tool_name: tool?.name || null,
      });
    } else if (item.type === "user") {
      const content = Array.isArray(item.message?.content) ? item.message.content : [];
      for (const part of content) {
        if (part?.type === "tool_result") {
          events.push({
            type: "tool_result",
            session_id: sessionId,
            payload: { is_error: Boolean(part.is_error) },
          });
        }
      }
    } else if (item.type === "stream_event") {
      const delta = item.event?.delta;
      if (item.event?.type === "content_block_delta" && typeof delta?.text === "string") {
        events.push({ type: "text_delta", session_id: sessionId, text: trimmed(delta.text) });
      }
    } else if (item.type === "result") {
      sessionId = item.session_id || sessionId;
      events.push({
        type: "completed",
        session_id: sessionId,
        payload: {
          subtype: item.subtype,
          is_error: Boolean(item.is_error),
          num_turns: item.num_turns,
        },
      });
    }
  }
  return events;
}

async function walkNative(root, base) {
  if (!existsSync(root)) return [];
  const files = [];
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const full = join(directory, entry.name);
      if (entry.isDirectory()) await visit(full);
      else if (
        entry.isFile() &&
        (entry.name.endsWith(".jsonl") || entry.name.endsWith(".meta.json"))
      ) {
        const info = await stat(full);
        const data = readFileSync(full);
        files.push({
          path: relative(base, full),
          size: info.size,
          sha256: createHash("sha256").update(data).digest("hex"),
        });
      }
    }
  }
  await visit(root);
  return files.sort((a, b) => a.path.localeCompare(b.path));
}

/** Raw native session files are authoritative for the trace (decision D8). */
export async function discoverTraceFiles(runtimeHome) {
  return walkNative(join(runtimeHome, ".claude"), runtimeHome);
}

/**
 * Run one Claude Code session. The raw stdout is persisted verbatim next to
 * the live events so M6 can parse from the authoritative native file.
 */
export async function runClaudeCode({
  prompt,
  model,
  workspace,
  runtimeHome,
  gatewayBaseUrl,
  gatewayToken,
  maxTurns = 40,
  executionImageDigest = null,
  timeoutMs = 3600_000,
  command = "claude",
  spawnImpl = spawn,
}) {
  mkdirSync(join(workspace, "trace"), { recursive: true });
  mkdirSync(runtimeHome, { recursive: true });
  const spec = buildClaudeCommand({
    prompt,
    model,
    workspace,
    runtimeHome,
    gatewayBaseUrl,
    gatewayToken,
    maxTurns,
    executionImageDigest,
    command,
  });
  const stdoutPath = join(workspace, "trace", "claude-stream.jsonl");
  const stderrPath = join(workspace, "trace", "claude-stderr.log");
  const chunks = [];
  const errors = [];
  const child = spawnImpl(spec.command, spec.args, {
    cwd: spec.cwd,
    env: spec.env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
  child.stderr.on("data", (chunk) => errors.push(Buffer.from(chunk)));
  let killTimer = null;
  const timer = setTimeout(() => {
    try {
      child.kill("SIGTERM");
    } catch {
      // process already exited
    }
    // Claude Code can ignore SIGTERM while a gateway stream is open; make the
    // timeout terminal so the enclosing container exits instead of hanging.
    killTimer = setTimeout(() => {
      try {
        child.kill("SIGKILL");
      } catch {
        // process already exited
      }
    }, 5_000);
  }, timeoutMs);
  const exitCode = await new Promise((resolvePromise, reject) => {
    child.on("error", reject);
    child.on("close", (code) => resolvePromise(code ?? 1));
  });
  clearTimeout(timer);
  if (killTimer) clearTimeout(killTimer);
  const stdout = Buffer.concat(chunks).toString("utf8");
  const stderr = Buffer.concat(errors).toString("utf8");
  // The agent may have cleaned untracked directories while working; re-create
  // the trace directory so a failed run still preserves its output.
  mkdirSync(join(workspace, "trace"), { recursive: true });
  writeFileSync(stdoutPath, stdout);
  writeFileSync(stderrPath, stderr);
  const events = consumeStreamLines(stdout.split("\n"));
  const traces = await discoverTraceFiles(runtimeHome);
  return {
    exitCode,
    events,
    traces,
    stdoutPath,
    stderrPath,
    command: {
      command: spec.command,
      args: spec.args,
      cwd: spec.cwd,
      env_keys: Object.keys(spec.env).sort(),
      execution_image_digest: spec.executionImageDigest,
    },
  };
}

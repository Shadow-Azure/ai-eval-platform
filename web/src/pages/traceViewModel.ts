import type { TraceSession, TraceSpan } from "../api/traces";

/**
 * Pure presentation model for the trace viewer.
 *
 * The backend keeps the canonical span graph; this module only derives
 * readable phases and steps from the selected session's spans. No IDs or
 * case-specific keys are hard-coded, so an unfamiliar trace simply falls
 * back to a single phase instead of crashing.
 */

export type TraceStepCategory = "explore" | "fix" | "verify" | "summary";

export type TraceStepKind =
  | "bash"
  | "edit"
  | "write"
  | "read"
  | "search"
  | "skill"
  | "subagent"
  | "thinking"
  | "llm"
  | "message"
  | "tool";

export interface TraceDiffLine {
  type: "add" | "del" | "ctx";
  lineNo: number | null;
  text: string;
}

export interface TraceReadLine {
  lineNo: number;
  text: string;
}

export interface TraceStep {
  id: string;
  span: TraceSpan;
  /** Paired tool_result spans and other spans that belong to the step. */
  related: TraceSpan[];
  spans: TraceSpan[];
  kind: TraceStepKind;
  category: TraceStepCategory | null;
  phaseId: string | null;
  toolName: string | null;
  title: string;
  summary: string;
  bodyText: string | null;
  inputText: string | null;
  outputText: string | null;
  toolInput: Record<string, unknown> | null;
  filePath: string | null;
  command: string | null;
  exitCode: number | null;
  statusText: string;
  error: boolean;
  durationMs: number | null;
  model: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  cost: string | null;
  diff: TraceDiffLine[];
  readLines: TraceReadLine[];
  outputTruncated: boolean;
  childSession: TraceSession | null;
  childSessionId: string | null;
  childAgentName: string | null;
}

export interface TracePhase {
  id: string;
  index: number;
  category: TraceStepCategory;
  label: string;
  description: string;
  summary: string;
  steps: TraceStep[];
  spanIds: string[];
  spanCount: number;
  stepCount: number;
  toolCount: number;
  errorCount: number;
  startedAt: string | null;
  endedAt: string | null;
  durationMs: number | null;
  firstOrdinal: number;
  lastOrdinal: number;
}

export interface TraceTask {
  span: TraceSpan;
  title: string;
  body: string;
}

export interface TraceViewModel {
  spans: TraceSpan[];
  steps: TraceStep[];
  phases: TracePhase[];
  task: TraceTask | null;
  phaseByStepId: Map<string, TracePhase>;
  stepBySpanId: Map<string, TraceStep>;
  spanById: Map<string, TraceSpan>;
  toolCount: number;
  errorCount: number;
  zeroDurationCount: number;
  inputTokens: number;
  outputTokens: number;
  cost: number | null;
  stashCount: number;
}

export interface TraceTimelineRow {
  phase: TracePhase;
  leftPercent: number;
  widthPercent: number;
  durationMs: number | null;
  startedAt: string | null;
  endedAt: string | null;
  zeroWidth: boolean;
  ordinalOnly: boolean;
}

export interface TraceTimeline {
  comparable: boolean;
  ordinalOnly: boolean;
  startAt: string | null;
  endAt: string | null;
  totalMs: number;
  rows: TraceTimelineRow[];
  zeroDurationCount: number;
  totalSpanCount: number;
  gapMs: number;
}

const MAX_DIFF_LINES = 240;
const MAX_READ_LINES = 220;

const EDIT_TOOLS = new Set([
  "edit",
  "multiedit",
  "multi_edit",
  "str_replace",
  "str_replace_editor",
  "strreplace",
  "apply_patch",
  "patch",
  "notebookedit",
]);
const WRITE_TOOLS = new Set([
  "write",
  "write_file",
  "create",
  "create_file",
  "notebookwrite",
]);
const READ_TOOLS = new Set(["read", "notebookread", "view", "open_file"]);
const SEARCH_TOOLS = new Set([
  "grep",
  "rg",
  "glob",
  "search",
  "find",
  "ls",
  "list",
  "websearch",
  "web_search",
  "webfetch",
  "web_fetch",
]);
const SKILL_TOOLS = new Set(["skill", "skills", "useskill", "use_skill"]);
const SUBAGENT_TOOLS = new Set([
  "task",
  "agent",
  "subagent",
  "sub_agent",
  "delegate",
  "spawn",
  "dispatch_agent",
]);
const BASH_TOOLS = new Set([
  "bash",
  "shell",
  "exec",
  "execute",
  "terminal",
  "run",
  "run_command",
  "command",
  "bashoutput",
]);

const GREP_TOOL_NAMES = /(^|[_\-.])(grep|rg|ag|ack|ripgrep)($|[_\-.]|$)/i;
const GREP_COMMAND = /(^|[\s;&|])(grep|rg|ag|ack|ripgrep)\b/i;

const VERIFY_PATTERNS: RegExp[] = [
  /\b(pytest|py\.test|unittest|tox|nox)\b/i,
  /\b(npm|pnpm|yarn|bun)\s+(run\s+)?(test|tests|lint|typecheck|type-check|build|check)\b/i,
  /\b(jest|vitest|mocha|ava|phpunit|rspec|karma|playwright|cypress)\b/i,
  /\b(go\s+test|cargo\s+(test|check|clippy)|mvn\s+(test|verify|package)|gradlew?\s+(test|check|build))\b/i,
  /\b(tsc|eslint|ruff|mypy|pyright|flake8|pylint|prettier|stylelint)\b/i,
  /\bgit\s+stash\b/i,
  /\b(make|just)\s+(test|check|lint)\b/i,
  /\b(compile|build|typecheck|lint)\b/i,
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function firstString(
  record: Record<string, unknown> | null,
  ...keys: string[]
): string | null {
  if (!record) return null;
  for (const key of keys) {
    const value = stringValue(record[key]);
    if (value) return value;
  }
  return null;
}

function attrString(span: TraceSpan, key: string): string | null {
  return stringValue(span.attributes[key]);
}

function oneLine(value: string | null | undefined, limit = 160): string {
  if (!value) return "";
  const collapsed = value.replace(/\s+/g, " ").trim();
  if (collapsed.length <= limit) return collapsed;
  return `${collapsed.slice(0, Math.max(1, limit - 1))}…`;
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function truncateText(value: string, limit: number): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit)}\n… 已截断，完整内容见「原始」或受控原文`;
}

export function parseToolInput(
  value: string | null | undefined,
): Record<string, unknown> | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed.startsWith("{")) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function isToolResultSpan(span: TraceSpan): boolean {
  const name = (span.name ?? "").toLowerCase();
  return (
    span.kind === "TOOL" &&
    (name === "tool_result" ||
      name === "tool-result" ||
      span.native_event_id?.startsWith("tool-result:") === true)
  );
}

function isRootAgentSpan(span: TraceSpan): boolean {
  return span.kind === "AGENT" && !span.parent_span_id;
}

function isToolStepSpan(span: TraceSpan): boolean {
  if (span.kind === "TOOL") return true;
  return span.kind === "AGENT" && Boolean(span.parent_span_id);
}

function isToolishKind(kind: TraceStepKind): boolean {
  return !["thinking", "llm", "message"].includes(kind);
}

function stepDuration(span: TraceSpan): number | null {
  if (span.duration_ms !== null) return span.duration_ms;
  if (!span.started_at || !span.ended_at) return null;
  const started = Date.parse(span.started_at);
  const ended = Date.parse(span.ended_at);
  if (!Number.isFinite(started) || !Number.isFinite(ended)) return null;
  return Math.max(0, ended - started);
}

function parseExitCode(
  input: Record<string, unknown> | null,
  output: string | null,
): number | null {
  const direct = numberValue(input?.exit_code ?? input?.exitCode);
  if (direct !== null) return direct;
  if (!output) return null;
  const match = /(?:exit\s*code|exitcode|exit_code)\D{0,6}(-?\d+)/i.exec(
    output,
  );
  if (!match) return null;
  const parsed = Number.parseInt(match[1], 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function hasFailureStatus(span: TraceSpan | null): boolean {
  if (!span) return false;
  const status = span.status ?? "";
  return /fail|error|timeout|cancel/i.test(status);
}

function resultHasError(result: TraceSpan): boolean {
  const errorType = attrString(result, "error.type");
  if (errorType) return true;
  const isError = result.attributes.is_error;
  return isError === true || hasFailureStatus(result);
}

function resultLabel(result: TraceSpan | null): string | null {
  if (!result) return null;
  const status = result.status?.trim();
  if (!status) return null;
  if (/fail|error|timeout|cancel/i.test(status)) return "失败";
  if (/success|succeed|complete|ok|done/i.test(status)) return "完成";
  return status;
}

function passedLabel(output: string | null): string | null {
  if (!output) return null;
  const failed = /(\d[\d,]*)\s+failed\b/i.exec(output);
  if (failed && Number.parseInt(failed[1].replace(/,/g, ""), 10) > 0) {
    return `${failed[1].replace(/,/g, "")} failed`;
  }
  const passed = /(\d[\d,]*)\s+passed\b/i.exec(output);
  if (passed) return `${passed[1].replace(/,/g, "")} passed`;
  return failed ? `${failed[1].replace(/,/g, "")} failed` : null;
}

function toolInputText(span: TraceSpan): string | null {
  return span.content_preview;
}

function resolveFilePath(input: Record<string, unknown> | null): string | null {
  return firstString(
    input,
    "file_path",
    "filePath",
    "path",
    "notebook_path",
    "target_file",
    "filename",
  );
}

function resolveCommand(
  input: Record<string, unknown> | null,
  fallback: string | null,
): string | null {
  if (!input) {
    return fallback && !fallback.trim().startsWith("{") ? fallback : null;
  }
  const command = input.command ?? input.cmd ?? input.script ?? input.code;
  if (typeof command === "string" && command.trim()) return command;
  if (Array.isArray(command)) {
    const joined = command
      .filter((item): item is string => typeof item === "string")
      .join(" ");
    if (joined.trim()) return joined;
  }
  return fallback && !fallback.trim().startsWith("{") ? fallback : null;
}

function classifyToolKind(name: string | null, kind: string): TraceStepKind {
  const normalized = (name ?? "").trim().toLowerCase();
  if (SKILL_TOOLS.has(normalized)) return "skill";
  if (SUBAGENT_TOOLS.has(normalized) || kind === "AGENT") return "subagent";
  if (EDIT_TOOLS.has(normalized)) return "edit";
  if (WRITE_TOOLS.has(normalized)) return "write";
  if (READ_TOOLS.has(normalized)) return "read";
  if (SEARCH_TOOLS.has(normalized)) return "search";
  if (BASH_TOOLS.has(normalized)) return "bash";
  return "tool";
}

function isVerifyCommand(command: string | null): boolean {
  if (!command) return false;
  return VERIFY_PATTERNS.some((pattern) => pattern.test(command));
}

function categoryForTool(
  kind: TraceStepKind,
  command: string | null,
): TraceStepCategory | null {
  if (kind === "edit" || kind === "write") return "fix";
  if (kind === "bash") return isVerifyCommand(command) ? "verify" : "explore";
  if (
    kind === "skill" ||
    kind === "subagent" ||
    kind === "read" ||
    kind === "search"
  ) {
    return "explore";
  }
  if (kind === "tool") return "explore";
  return null;
}

function isLocateBoundary(step: TraceStep): boolean {
  if (step.kind === "read") return true;
  if (step.kind === "search") {
    return !GREP_TOOL_NAMES.test(step.toolName ?? "");
  }
  if (step.kind === "tool") {
    return !GREP_TOOL_NAMES.test(step.toolName ?? "");
  }
  if (step.kind === "bash") {
    return !GREP_COMMAND.test(step.command ?? "");
  }
  return false;
}

function pushUnique(target: TraceSpan[], values: TraceSpan[]): void {
  const seen = new Set(target.map((item) => item.id));
  for (const value of values) {
    if (!seen.has(value.id)) {
      target.push(value);
      seen.add(value.id);
    }
  }
}

function buildDiff(
  oldText: string | null,
  newText: string | null,
  lineNumbers: boolean,
): TraceDiffLine[] {
  if (!oldText && !newText) return [];
  if (!oldText) {
    return (newText ?? "")
      .split(/\r?\n/)
      .slice(0, MAX_DIFF_LINES)
      .map((text, index) => ({
        type: "add" as const,
        lineNo: lineNumbers ? index + 1 : null,
        text,
      }));
  }
  if (!newText) {
    return oldText
      .split(/\r?\n/)
      .slice(0, MAX_DIFF_LINES)
      .map((text, index) => ({
        type: "del" as const,
        lineNo: lineNumbers ? index + 1 : null,
        text,
      }));
  }

  const oldLines = oldText.split(/\r?\n/);
  const newLines = newText.split(/\r?\n/);
  let prefix = 0;
  while (
    prefix < oldLines.length &&
    prefix < newLines.length &&
    oldLines[prefix] === newLines[prefix]
  ) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix < oldLines.length - prefix &&
    suffix < newLines.length - prefix &&
    oldLines[oldLines.length - 1 - suffix] ===
      newLines[newLines.length - 1 - suffix]
  ) {
    suffix += 1;
  }

  const result: TraceDiffLine[] = [];
  const contextStart = Math.max(0, prefix - 2);
  for (let index = contextStart; index < prefix; index += 1) {
    result.push({
      type: "ctx",
      lineNo: lineNumbers ? index + 1 : null,
      text: oldLines[index],
    });
  }
  for (let index = prefix; index < oldLines.length - suffix; index += 1) {
    result.push({
      type: "del",
      lineNo: lineNumbers ? index + 1 : null,
      text: oldLines[index],
    });
  }
  for (let index = prefix; index < newLines.length - suffix; index += 1) {
    result.push({
      type: "add",
      lineNo: lineNumbers ? index + 1 : null,
      text: newLines[index],
    });
  }
  for (
    let index = oldLines.length - suffix;
    index < oldLines.length;
    index += 1
  ) {
    result.push({
      type: "ctx",
      lineNo: lineNumbers ? index + 1 : null,
      text: oldLines[index],
    });
  }
  return result.slice(0, MAX_DIFF_LINES);
}

function buildReadLines(output: string | null): TraceReadLine[] {
  if (!output) return [];
  const lines = output.split(/\r?\n/);
  const arrowMatches = lines.map((line) =>
    /^\s*(\d+)\s*[→|]\s?(.*)$/.exec(line),
  );
  const tabMatches = lines.map((line) => /^\s*(\d+)\t(.*)$/.exec(line));
  const matched = arrowMatches.some(Boolean) ? arrowMatches : tabMatches;
  const numberedCount = matched.filter(Boolean).length;
  const useNumbers =
    numberedCount >= Math.max(1, Math.ceil(lines.length * 0.6));
  return lines.slice(0, MAX_READ_LINES).map((line, index) => {
    const match = useNumbers ? matched[index] : null;
    return {
      lineNo: match ? Number.parseInt(match[1], 10) : index + 1,
      text: match ? match[2] : line,
    };
  });
}

function flattenSessions(sessions: TraceSession[]): TraceSession[] {
  const result: TraceSession[] = [];
  const seen = new Set<string>();
  const visit = (session: TraceSession) => {
    if (seen.has(session.id)) return;
    seen.add(session.id);
    result.push(session);
    for (const child of session.children) visit(child);
  };
  for (const session of sessions) visit(session);
  return result;
}

function relationString(session: TraceSession, keys: string[]): string | null {
  for (const key of keys) {
    const value = stringValue(session.relation_evidence[key]);
    if (value) return value;
  }
  return null;
}

function sessionIndex(sessions: TraceSession[]): {
  byToolCallId: Map<string, TraceSession>;
  byAgentId: Map<string, TraceSession>;
} {
  const byToolCallId = new Map<string, TraceSession>();
  const byAgentId = new Map<string, TraceSession>();
  for (const session of flattenSessions(sessions)) {
    const toolUseId = relationString(session, [
      "tool_use_id",
      "toolUseId",
      "tool_call_id",
    ]);
    if (toolUseId && !byToolCallId.has(toolUseId))
      byToolCallId.set(toolUseId, session);
    if (session.agent_id && !byAgentId.has(session.agent_id)) {
      byAgentId.set(session.agent_id, session);
    }
  }
  return { byToolCallId, byAgentId };
}

function toolCallId(span: TraceSpan): string | null {
  const direct = attrString(span, "gen_ai.tool.call.id");
  if (direct) return direct;
  if (span.native_event_id?.startsWith("tool:")) {
    return span.native_event_id.slice("tool:".length) || null;
  }
  return null;
}

function buildToolStep(
  span: TraceSpan,
  results: TraceSpan[],
  indexes: ReturnType<typeof sessionIndex>,
): TraceStep {
  const inputText = toolInputText(span);
  const toolInput = parseToolInput(inputText);
  const toolName = span.name ?? "tool";
  const kind = classifyToolKind(toolName, span.kind);
  const related: TraceSpan[] = [];
  pushUnique(related, results);
  const outputText =
    results
      .map((item) => item.content_preview)
      .filter((item): item is string => Boolean(item))
      .join("\n\n") || null;
  const outputTruncated = results.some(
    (item) => item.attributes["content.truncated"] === true,
  );
  const command = kind === "bash" ? resolveCommand(toolInput, inputText) : null;
  const filePath = resolveFilePath(toolInput);
  const childAgentId = attrString(span, "claude.child.agent_id");
  const callId = toolCallId(span);
  const childSession =
    (callId ? indexes.byToolCallId.get(callId) : null) ??
    (childAgentId ? indexes.byAgentId.get(childAgentId) : null) ??
    null;
  const error = results.some(resultHasError) || hasFailureStatus(span);
  const exitCode = parseExitCode(toolInput, outputText);
  const passed = passedLabel(outputText);
  const statusText = error
    ? "失败"
    : exitCode !== null && exitCode !== 0
      ? `退出码 ${exitCode}`
      : (passed ??
        resultLabel(results[0] ?? null) ??
        (results.length ? "完成" : "已记录"));
  const oldString = firstString(
    toolInput,
    "old_string",
    "oldString",
    "old_text",
  );
  const newString = firstString(
    toolInput,
    "new_string",
    "newString",
    "new_text",
  );
  const writeContent = firstString(toolInput, "content", "text", "file_text");
  const diff =
    kind === "edit"
      ? buildDiff(oldString, newString, false)
      : kind === "write"
        ? buildDiff(null, writeContent, true)
        : [];
  const readLines = kind === "read" ? buildReadLines(outputText) : [];
  const description = firstString(
    toolInput,
    "description",
    "summary",
    "purpose",
  );
  const summary = (() => {
    if (kind === "bash") return description ?? oneLine(command, 110);
    if (kind === "edit" || kind === "write") {
      const name = filePath ? filePath.split("/").filter(Boolean).pop() : null;
      const adds = diff.filter((line) => line.type === "add").length;
      const dels = diff.filter((line) => line.type === "del").length;
      return (
        [name, adds || dels ? `+${adds} / −${dels}` : null]
          .filter(Boolean)
          .join(" · ") || "文件变更"
      );
    }
    if (kind === "read")
      return (
        description ??
        (readLines.length ? `${readLines.length} 行` : "读取文件内容")
      );
    if (kind === "search") {
      const pattern = firstString(
        toolInput,
        "pattern",
        "query",
        "glob",
        "path",
      );
      return description ?? pattern ?? "检索代码";
    }
    if (kind === "skill") {
      const skill = firstString(toolInput, "skill", "name", "skill_name");
      return description ?? (skill ? `技能 ${skill}` : "调用技能");
    }
    if (kind === "subagent") {
      return (
        description ??
        (childSession
          ? `${childSession.span_count} spans · 子 Agent`
          : "派生子 Agent")
      );
    }
    return (
      description ??
      firstString(toolInput, "prompt", "query", "path") ??
      "工具调用"
    );
  })();
  const title = (() => {
    if (kind === "bash") return oneLine(command, 180) || "Bash";
    if (kind === "edit" || kind === "write" || kind === "read")
      return filePath ?? toolName;
    if (kind === "search") {
      return (
        firstString(toolInput, "pattern", "query", "glob", "path") ??
        description ??
        toolName
      );
    }
    if (kind === "skill") {
      return firstString(toolInput, "skill", "name", "skill_name") ?? toolName;
    }
    if (kind === "subagent") {
      return (
        childSession?.agent_name ??
        childAgentId ??
        firstString(toolInput, "description", "agent_type", "prompt") ??
        toolName
      );
    }
    return toolName;
  })();

  return {
    id: span.id,
    span,
    related,
    spans: [span, ...related],
    kind,
    category: categoryForTool(kind, command),
    phaseId: null,
    toolName,
    title: oneLine(title, 180),
    summary: oneLine(summary, 150),
    bodyText: null,
    inputText,
    outputText,
    toolInput,
    filePath,
    command,
    exitCode,
    statusText,
    error: error || (exitCode !== null && exitCode !== 0),
    durationMs: stepDuration(span),
    model: null,
    inputTokens: span.input_tokens,
    outputTokens: span.output_tokens,
    cost: span.cost,
    diff,
    readLines,
    outputTruncated,
    childSession,
    childSessionId: childSession?.id ?? null,
    childAgentName: childSession?.agent_name ?? childAgentId,
  };
}

function buildTextStep(
  span: TraceSpan,
  kind: "thinking" | "llm" | "message",
  parentLlm: TraceSpan | null,
): TraceStep {
  const bodyText = span.content_hidden
    ? "推理内容已按权限隐藏，只保留类型与用量信息。"
    : span.content_preview;
  const model =
    attrString(span, "gen_ai.request.model") ??
    (parentLlm ? attrString(parentLlm, "gen_ai.request.model") : null);
  const tokensIn = span.input_tokens ?? parentLlm?.input_tokens ?? null;
  const tokensOut = span.output_tokens ?? parentLlm?.output_tokens ?? null;
  const cost = span.cost ?? parentLlm?.cost ?? null;
  const title =
    kind === "thinking"
      ? "思考"
      : kind === "llm"
        ? (model ?? "模型调用")
        : "助手消息";
  const summary =
    kind === "llm"
      ? [
          model,
          tokensIn !== null || tokensOut !== null
            ? `${tokensIn ?? 0} in · ${tokensOut ?? 0} out`
            : null,
        ]
          .filter(Boolean)
          .join(" · ")
      : oneLine(bodyText, 120) ||
        (kind === "thinking" ? "推理片段" : "消息内容");
  return {
    id: span.id,
    span,
    related: [],
    spans: [span],
    kind,
    category: null,
    phaseId: null,
    toolName: null,
    title,
    summary,
    bodyText,
    inputText: null,
    outputText: null,
    toolInput: null,
    filePath: null,
    command: null,
    exitCode: null,
    statusText: kind === "llm" ? "完成" : "完成",
    error: hasFailureStatus(span),
    durationMs: stepDuration(span),
    model,
    inputTokens: tokensIn,
    outputTokens: tokensOut,
    cost,
    diff: [],
    readLines: [],
    outputTruncated: false,
    childSession: null,
    childSessionId: null,
    childAgentName: null,
  };
}

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function stepWindow(steps: TraceStep[]): {
  startedAt: string | null;
  endedAt: string | null;
} {
  let start: number | null = null;
  let end: number | null = null;
  let startLabel: string | null = null;
  let endLabel: string | null = null;
  for (const step of steps) {
    for (const span of step.spans) {
      if (span.kind === "AGENT" && !span.parent_span_id) continue;
      const started = timestamp(span.started_at);
      if (started !== null && (start === null || started < start)) {
        start = started;
        startLabel = span.started_at;
      }
      const ended = timestamp(span.ended_at) ?? started;
      if (ended !== null && (end === null || ended > end)) {
        end = ended;
        endLabel = span.ended_at ?? span.started_at;
      }
    }
  }
  if (start !== null && end !== null && end < start) {
    return { startedAt: startLabel, endedAt: endLabel };
  }
  return { startedAt: startLabel, endedAt: endLabel };
}

function phaseDuration(
  startedAt: string | null,
  endedAt: string | null,
): number | null {
  const started = timestamp(startedAt);
  const ended = timestamp(endedAt);
  if (started === null || ended === null || ended < started) return null;
  return ended - started;
}

interface WorkingPhase {
  category: TraceStepCategory;
  items: TraceStep[];
}

function splitExplorePhase(run: WorkingPhase): WorkingPhase[] {
  if (run.category !== "explore") return [run];
  const splitAt = run.items.findIndex((step, index) => {
    if (index === 0 || !isLocateBoundary(step)) return false;
    return run.items.slice(0, index).some((item) => isToolishKind(item.kind));
  });
  if (splitAt <= 0) return [run];
  return [
    { category: "explore", items: run.items.slice(0, splitAt) },
    { category: "explore", items: run.items.slice(splitAt) },
  ];
}

function phaseLabel(
  category: TraceStepCategory,
  steps: TraceStep[],
  exploreIndex: number,
  exploreSplit: boolean,
): { label: string; description: string } {
  if (category === "fix") {
    const files = [
      ...new Set(
        steps
          .map((step) => step.filePath)
          .filter((value): value is string => Boolean(value))
          .map((value) => value.split("/").filter(Boolean).pop() ?? value),
      ),
    ].slice(0, 2);
    return {
      label: "实施修复",
      description: files.length ? `修改 ${files.join(" / ")}` : "修改生产代码",
    };
  }
  if (category === "verify") {
    return { label: "回归验证", description: "运行测试、构建与回归检查" };
  }
  if (category === "summary") {
    return {
      label: "总结",
      description: steps[0]?.summary || "汇总结论与验证结果",
    };
  }
  if (exploreSplit) {
    return exploreIndex === 0
      ? { label: "理解", description: "读取问题描述与约束" }
      : { label: "定位", description: "追踪相关代码、文件与调用关系" };
  }
  return exploreIndex === 0
    ? { label: "探索与定位", description: "阅读任务、检索代码与调用关系" }
    : { label: "补充探索", description: "继续检索与确认证据" };
}

function categoryOfTool(step: TraceStep): TraceStepCategory | null {
  if (step.category) return step.category;
  return categoryForTool(step.kind, step.command);
}

export function buildTraceViewModel(
  spans: TraceSpan[],
  sessions: TraceSession[],
): TraceViewModel {
  const sorted = [...spans].sort((left, right) => {
    if (left.ordinal !== right.ordinal) return left.ordinal - right.ordinal;
    return left.id.localeCompare(right.id);
  });
  const spanById = new Map(sorted.map((span) => [span.id, span]));
  const results = sorted.filter(isToolResultSpan);
  const resultsByParent = new Map<string, TraceSpan[]>();
  const resultsByCallId = new Map<string, TraceSpan[]>();
  for (const result of results) {
    if (result.parent_span_id) {
      const bucket = resultsByParent.get(result.parent_span_id) ?? [];
      bucket.push(result);
      resultsByParent.set(result.parent_span_id, bucket);
    }
    const callId =
      toolCallId(result) ?? attrString(result, "gen_ai.tool.call.id");
    if (callId) {
      const bucket = resultsByCallId.get(callId) ?? [];
      bucket.push(result);
      resultsByCallId.set(callId, bucket);
    }
  }

  const rawSteps: TraceStep[] = [];
  const llmSpans = sorted.filter((span) => span.kind === "LLM");
  const llmById = new Map(llmSpans.map((span) => [span.id, span]));
  let taskSpan: TraceSpan | null = null;
  const indexes = sessionIndex(sessions);

  for (const span of sorted) {
    if (isRootAgentSpan(span) || isToolResultSpan(span)) continue;
    if (isToolStepSpan(span)) {
      const callId = toolCallId(span);
      const related = [
        ...(resultsByParent.get(span.id) ?? []),
        ...(callId ? (resultsByCallId.get(callId) ?? []) : []),
      ];
      rawSteps.push(buildToolStep(span, related, indexes));
      continue;
    }
    if (span.kind === "THINKING") {
      rawSteps.push(
        buildTextStep(
          span,
          "thinking",
          span.parent_span_id
            ? (llmById.get(span.parent_span_id) ?? null)
            : null,
        ),
      );
      continue;
    }
    if (span.kind === "LLM") continue;
    if (span.kind === "MESSAGE") {
      const name = (span.name ?? "").toLowerCase();
      const isUser = name === "user" || name === "human" || name === "system";
      if (isUser) {
        if (!taskSpan) taskSpan = span;
        continue;
      }
      if (span.content_preview) {
        rawSteps.push(
          buildTextStep(
            span,
            "message",
            span.parent_span_id
              ? (llmById.get(span.parent_span_id) ?? null)
              : null,
          ),
        );
      }
      continue;
    }
    if (span.content_preview) {
      rawSteps.push(buildTextStep(span, "message", null));
    }
  }

  // An LLM span is the container for its thinking/tool/message children. Only
  // surface it as a standalone step when the trace has no child evidence.
  const parentsWithSteps = new Set<string>();
  for (const step of rawSteps) {
    if (step.span.parent_span_id)
      parentsWithSteps.add(step.span.parent_span_id);
  }
  for (const span of llmSpans) {
    if (parentsWithSteps.has(span.id)) continue;
    rawSteps.push(buildTextStep(span, "llm", null));
  }

  rawSteps.sort((left, right) => {
    if (left.span.ordinal !== right.span.ordinal)
      return left.span.ordinal - right.span.ordinal;
    return left.span.id.localeCompare(right.span.id);
  });

  const toolSteps = rawSteps.filter((step) => isToolishKind(step.kind));
  const lastToolOrdinal = toolSteps.reduce(
    (current, step) => Math.max(current, step.span.ordinal),
    -1,
  );
  const trailingMessages = rawSteps.filter(
    (step) => step.kind === "message" && step.span.ordinal > lastToolOrdinal,
  );
  const summaryStep = trailingMessages[trailingMessages.length - 1] ?? null;
  if (summaryStep) summaryStep.category = "summary";
  for (const step of rawSteps) {
    if (step.category === null) step.category = categoryOfTool(step);
    if (step.kind === "message" && step !== summaryStep) step.category = null;
    if (step.kind === "thinking" || step.kind === "llm") step.category = null;
  }

  const runs: WorkingPhase[] = [];
  let pending: TraceStep[] = [];
  for (const step of rawSteps) {
    if (!step.category) {
      // Hold reasoning until the next categorized action: a thought before a
      // test run belongs to verification, not to the preceding edit.
      pending.push(step);
      continue;
    }
    const last = runs[runs.length - 1];
    // Explore after a fix or verification is usually the model checking its
    // own work, not a new reading phase. Keep it in the adjacent phase so the
    // trace reads as fix -> verify instead of growing extra "补充探索" rows.
    if (
      step.category === "explore" &&
      last &&
      (last.category === "fix" ||
        last.category === "verify" ||
        last.category === "summary")
    ) {
      last.items.push(...pending, step);
      pending = [];
      continue;
    }
    if (last && last.category === step.category) {
      last.items.push(...pending, step);
      pending = [];
    } else {
      runs.push({ category: step.category, items: [...pending, step] });
      pending = [];
    }
  }
  if (pending.length && runs.length) {
    runs[runs.length - 1].items.push(...pending);
  }
  if (runs.length === 0 && rawSteps.length > 0) {
    // A trace with only thinking/message spans still deserves a readable view.
    runs.push({ category: "explore", items: [...rawSteps] });
  }

  const working: Array<{
    phase: WorkingPhase;
    exploreSplit: boolean;
    exploreIndex: number;
  }> = [];
  let exploreIndex = 0;
  for (const run of runs) {
    const parts = splitExplorePhase(run);
    const split = parts.length > 1;
    for (const part of parts) {
      working.push({
        phase: part,
        exploreSplit: split,
        exploreIndex: part.category === "explore" ? exploreIndex : 0,
      });
      if (part.category === "explore") exploreIndex += 1;
    }
  }

  const phases: TracePhase[] = working.map(
    ({ phase, exploreSplit, exploreIndex: exploreNo }, index) => {
      const id = `phase-${index + 1}`;
      for (const step of phase.items) {
        step.phaseId = id;
      }
      const anchor = phase.items[0]?.span.ordinal ?? 0;
      const lastOrdinal = phase.items.reduce(
        (current, step) =>
          Math.max(
            current,
            step.spans.reduce(
              (nested, span) => Math.max(nested, span.ordinal),
              current,
            ),
          ),
        anchor,
      );
      const window = stepWindow(phase.items);
      const label = phaseLabel(
        phase.category,
        phase.items,
        exploreNo,
        exploreSplit,
      );
      return {
        id,
        index,
        category: phase.category,
        label: label.label,
        description: label.description,
        summary: phase.items[0]?.summary ?? label.description,
        steps: phase.items,
        spanIds: [],
        spanCount: 0,
        stepCount: phase.items.length,
        toolCount: phase.items.filter((step) => isToolishKind(step.kind))
          .length,
        errorCount: phase.items.filter((step) => step.error).length,
        startedAt: window.startedAt,
        endedAt: window.endedAt,
        durationMs: phaseDuration(window.startedAt, window.endedAt),
        firstOrdinal: anchor,
        lastOrdinal,
      };
    },
  );

  const stepBySpanId = new Map<string, TraceStep>();
  const phaseByStepId = new Map<string, TracePhase>();
  const assigned = new Set<string>();
  for (const phase of phases) {
    for (const step of phase.steps) {
      stepBySpanId.set(step.span.id, step);
      phaseByStepId.set(step.id, phase);
      for (const span of step.spans) {
        assigned.add(span.id);
        if (!phase.spanIds.includes(span.id)) phase.spanIds.push(span.id);
      }
    }
  }
  if (phases.length === 0) {
    // Nothing recognizable found. Keep a neutral container so the page still
    // renders the raw session and avoids an empty crash state.
    const fallback: TracePhase = {
      id: "phase-1",
      index: 0,
      category: "explore",
      label: "执行轨迹",
      description: "暂未识别到可归并的步骤",
      summary: "原始 span 仍可在右侧检查器中查看",
      steps: [],
      spanIds: [],
      spanCount: 0,
      stepCount: 0,
      toolCount: 0,
      errorCount: 0,
      startedAt: null,
      endedAt: null,
      durationMs: null,
      firstOrdinal: 0,
      lastOrdinal: 0,
    };
    phases.push(fallback);
  }

  for (const span of sorted) {
    if (assigned.has(span.id)) continue;
    let target = phases[0];
    for (const phase of phases) {
      if (phase.firstOrdinal <= span.ordinal) target = phase;
      else break;
    }
    target.spanIds.push(span.id);
  }
  for (const phase of phases) {
    phase.spanIds.sort((left, right) => {
      const leftSpan = spanById.get(left);
      const rightSpan = spanById.get(right);
      return (leftSpan?.ordinal ?? 0) - (rightSpan?.ordinal ?? 0);
    });
    phase.spanCount = phase.spanIds.length;
    phase.errorCount = phase.steps.filter((step) => step.error).length;
  }

  const task = taskSpan
    ? {
        span: taskSpan,
        title: oneLine(taskSpan.content_preview, 160) || "用户任务",
        body: taskSpan.content_preview ?? "",
      }
    : null;

  const zeroDurationCount = sorted.filter((span) => {
    if (span.duration_ms === 0) return true;
    if (span.duration_ms !== null) return false;
    if (!span.started_at || !span.ended_at) return true;
    return span.started_at === span.ended_at;
  }).length;

  const costValues = sorted
    .map((span) => (span.cost ? Number.parseFloat(span.cost) : Number.NaN))
    .filter((value) => Number.isFinite(value));
  const inputTokens = sorted.reduce(
    (sum, span) => sum + (span.input_tokens ?? 0),
    0,
  );
  const outputTokens = sorted.reduce(
    (sum, span) => sum + (span.output_tokens ?? 0),
    0,
  );
  const stashCount = rawSteps.filter(
    (step) =>
      step.kind === "bash" && /\bgit\s+stash\b/i.test(step.command ?? ""),
  ).length;

  return {
    spans: sorted,
    steps: rawSteps,
    phases,
    task,
    phaseByStepId,
    stepBySpanId,
    spanById,
    toolCount: toolSteps.length,
    errorCount: rawSteps.filter((step) => step.error).length,
    zeroDurationCount,
    inputTokens,
    outputTokens,
    cost: costValues.length
      ? costValues.reduce((sum, value) => sum + value, 0)
      : null,
    stashCount,
  };
}

export function buildTraceTimeline(
  model: TraceViewModel,
  clockComparable: boolean,
): TraceTimeline {
  const totalSpanCount = model.spans.length;
  const start = model.phases
    .map((phase) => timestamp(phase.startedAt))
    .filter((value): value is number => value !== null);
  const end = model.phases
    .map((phase) => timestamp(phase.endedAt))
    .filter((value): value is number => value !== null);
  if (!clockComparable || start.length === 0 || end.length === 0) {
    return {
      comparable: false,
      ordinalOnly: true,
      startAt: null,
      endAt: null,
      totalMs: 0,
      rows: model.phases.map((phase, index) => ({
        phase,
        leftPercent:
          model.phases.length <= 1 ? 0 : (index / model.phases.length) * 100,
        widthPercent: 0,
        durationMs: phase.durationMs,
        startedAt: phase.startedAt,
        endedAt: phase.endedAt,
        zeroWidth: false,
        ordinalOnly: true,
      })),
      zeroDurationCount: model.zeroDurationCount,
      totalSpanCount,
      gapMs: 0,
    };
  }
  const base = Math.min(...start);
  const finish = Math.max(...end);
  const totalMs = finish - base;
  if (totalMs <= 0) {
    return {
      comparable: false,
      ordinalOnly: true,
      startAt: model.phases.find((phase) => phase.startedAt)?.startedAt ?? null,
      endAt: model.phases.find((phase) => phase.endedAt)?.endedAt ?? null,
      totalMs: 0,
      rows: model.phases.map((phase, index) => ({
        phase,
        leftPercent:
          model.phases.length <= 1 ? 0 : (index / model.phases.length) * 100,
        widthPercent: 0,
        durationMs: phase.durationMs,
        startedAt: phase.startedAt,
        endedAt: phase.endedAt,
        zeroWidth: false,
        ordinalOnly: true,
      })),
      zeroDurationCount: model.zeroDurationCount,
      totalSpanCount,
      gapMs: 0,
    };
  }

  const rows: TraceTimelineRow[] = model.phases.map((phase) => {
    const phaseStart = timestamp(phase.startedAt);
    const phaseEnd = timestamp(phase.endedAt);
    const valid =
      phaseStart !== null && phaseEnd !== null && phaseEnd >= phaseStart;
    const left = valid ? ((phaseStart - base) / totalMs) * 100 : 0;
    const width = valid ? ((phaseEnd - phaseStart) / totalMs) * 100 : 0;
    return {
      phase,
      leftPercent: Math.max(0, Math.min(100, Number(left.toFixed(4)))),
      widthPercent: Math.max(0, Math.min(100, Number(width.toFixed(4)))),
      durationMs: valid ? phaseEnd - phaseStart : null,
      startedAt: phase.startedAt,
      endedAt: phase.endedAt,
      zeroWidth: valid && phaseEnd === phaseStart,
      ordinalOnly: false,
    };
  });

  let gapMs = 0;
  let previousEnd: number | null = null;
  for (const row of [...rows].sort(
    (left, right) => left.leftPercent - right.leftPercent,
  )) {
    const rowStart = timestamp(row.startedAt);
    if (previousEnd !== null && rowStart !== null && rowStart > previousEnd) {
      gapMs += rowStart - previousEnd;
    }
    const rowEnd = timestamp(row.endedAt);
    if (rowEnd !== null && (previousEnd === null || rowEnd > previousEnd))
      previousEnd = rowEnd;
  }

  return {
    comparable: true,
    ordinalOnly: false,
    startAt: new Date(base).toISOString(),
    endAt: new Date(finish).toISOString(),
    totalMs,
    rows,
    zeroDurationCount: model.zeroDurationCount,
    totalSpanCount,
    gapMs,
  };
}

export function stepSearchText(step: TraceStep): string {
  return [
    step.title,
    step.summary,
    step.toolName,
    step.command,
    step.filePath,
    step.bodyText,
    step.inputText,
    step.outputText,
    step.childAgentName,
    step.model,
  ]
    .filter((value): value is string => Boolean(value))
    .join("\n")
    .toLowerCase();
}

export function stepMatchesQuery(step: TraceStep, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return normalized
    .split(/\s+/)
    .filter(Boolean)
    .every((part) => stepSearchText(step).includes(part));
}

export function truncateForDisplay(value: string, limit: number): string {
  return truncateText(value, limit);
}

export function formatStepInput(step: TraceStep): string {
  if (step.toolInput) return safeJson(step.toolInput);
  return step.inputText ?? "没有记录工具输入。";
}

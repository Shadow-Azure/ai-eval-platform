import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import {
  Alert,
  App as AntApp,
  Button,
  Drawer,
  Input,
  Modal,
  Segmented,
  Select,
  Skeleton,
  Spin,
  Tabs,
  Tag,
  Tooltip,
} from "antd";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  fetchEvaluationCase,
  type CaseTestSummary,
} from "../api/evalWorkbench";
import {
  createTraceFileDownloadUrl,
  createTraceSpanContentUrl,
  downloadTraceArchive,
  fetchAttemptTrace,
  fetchTraceSessionSpans,
  fetchTraceSpan,
  type TraceDownloadUrl,
  type TraceFile,
  type TraceSession,
  type TraceSpan,
} from "../api/traces";
import { useAuth } from "../app/auth";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { formatMoney } from "../utils/money";
import {
  buildArchiveTree,
  formatBytes,
  summarizeTraceExport,
} from "../features/traces/traceExportModel";
import {
  flattenSessionTree,
  markMissingParents,
  mergeSelectedSpan,
  mergeSpanPages,
  readSpanFilter,
  readTraceSelection,
  spanBelongsToSession,
  traceStatusForAttempt,
} from "../features/traces/traceModel";
import {
  buildTraceTimeline,
  buildTraceViewModel,
  formatStepInput,
  stepMatchesQuery,
  type TracePhase,
  type TraceStep,
  type TraceTimeline,
  type TraceViewModel,
} from "./traceViewModel";

const FILTERS = [
  { value: "all", label: "全部" },
  { value: "AGENT", label: "Agent" },
  { value: "LLM", label: "LLM" },
  { value: "TOOL", label: "Tool" },
  { value: "MESSAGE", label: "消息" },
  { value: "THINKING", label: "思考" },
] as const;

const STATUS_META: Record<string, { label: string; color: string }> = {
  ready: { label: "可用", color: "success" },
  degraded: { label: "已降级", color: "warning" },
  failed: { label: "解析失败", color: "error" },
  pending: { label: "待处理", color: "default" },
  parsing: { label: "处理中", color: "processing" },
  incomplete: { label: "采集不完整", color: "warning" },
  unlinked: { label: "未关联", color: "warning" },
};

type CenterView = "conversation" | "steps" | "timeline";

const CENTER_VIEWS: Array<{ value: CenterView; label: string }> = [
  { value: "conversation", label: "对话轨迹" },
  { value: "steps", label: "步骤" },
  { value: "timeline", label: "时间线" },
];

const STEP_KIND_LABEL: Record<TraceStep["kind"], string> = {
  bash: "Bash 命令",
  edit: "Edit 变更",
  write: "Write 文件",
  read: "Read 文件",
  search: "检索",
  skill: "Skill 技能",
  subagent: "Subagent 子 Agent",
  thinking: "思考",
  llm: "模型调用",
  message: "消息",
  tool: "工具调用",
};

const STEP_KIND_BADGE: Record<TraceStep["kind"], string> = {
  bash: "$",
  edit: "±",
  write: "+",
  read: "☰",
  search: "⌕",
  skill: "✦",
  subagent: "↳",
  thinking: "✦",
  llm: "AI",
  message: "…",
  tool: "○",
};

const CATEGORY_LABEL: Record<TracePhase["category"], string> = {
  explore: "探索",
  fix: "修复",
  verify: "验证",
  summary: "总结",
};

function durationLabel(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms === 0) return "0 ms";
  if (ms < 100) return "<0.1 s";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

function timeLabel(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}

function clockLabel(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("zh-CN", { hour12: false });
}

function compactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function formatCost(value: number | string | null | undefined): string {
  return formatMoney(value);
}

function textValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "—";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function filesByStatus(files: TraceFile[], status: string): TraceFile[] {
  return files.filter((item) => item.status === status);
}

function statusTag(status: string, meta = STATUS_META) {
  const item = meta[status] ?? { label: status || "未知", color: "default" };
  return <Tag color={item.color}>{item.label}</Tag>;
}

function stepStatusTone(step: TraceStep): string {
  if (step.error || /fail|error/i.test(step.statusText)) return "error";
  if (step.kind === "bash" && step.statusText.endsWith("passed")) return "ok";
  return "";
}

function testSummaryLabel(summary: CaseTestSummary | null | undefined): string {
  if (!summary) return "—";
  if (summary.failed > 0)
    return `${summary.passed} passed · ${summary.failed} failed`;
  if (summary.passed > 0) return `${summary.passed} passed`;
  if (summary.error > 0) return `${summary.error} error`;
  if (summary.skipped > 0) return `${summary.skipped} skipped`;
  return "—";
}

function useNarrowViewport(maxWidth = 1280): boolean {
  const [narrow, setNarrow] = useState<boolean>(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(`(max-width: ${maxWidth}px)`).matches;
  });
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia(`(max-width: ${maxWidth}px)`);
    const update = () => setNarrow(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [maxWidth]);
  return narrow;
}

function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText)
    return navigator.clipboard.writeText(value);
  return Promise.reject(new Error("当前浏览器不支持剪贴板写入"));
}

/** Prototype's `download` glyph; not in the shared Icon map yet. */
function DownloadGlyph({ size = 14 }: { size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
    </svg>
  );
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

interface SessionListProps {
  linked: Array<{ session: TraceSession; depth: number }>;
  unlinked: Array<{ session: TraceSession; depth: number }>;
  selectedId: string | null;
  onSelect: (session: TraceSession) => void;
}

function SessionList({
  linked,
  unlinked,
  selectedId,
  onSelect,
}: SessionListProps) {
  const renderItem = (
    item: { session: TraceSession; depth: number },
    group: "linked" | "unlinked",
  ) => (
    <button
      key={item.session.id}
      type="button"
      className={`trace-tree-item ${selectedId === item.session.id ? "active" : ""} ${
        item.depth ? `depth-${Math.min(item.depth, 3)}` : ""
      }`}
      onClick={() => onSelect(item.session)}
      aria-current={selectedId === item.session.id ? "true" : undefined}
      aria-label={`选择会话 ${item.session.agent_name ?? item.session.id}`}
    >
      <span
        className={`trace-session-mark ${group === "unlinked" ? "warn" : ""}`}
      >
        {group === "unlinked" ? "U" : "A"}
      </span>
      <span className="trace-tree-copy">
        <span className="truncate">
          {item.session.agent_name ?? item.session.id.slice(0, 12)}
        </span>
        <span className="trace-tree-meta">
          {group === "unlinked"
            ? "未关联会话"
            : item.session.agent_id
              ? `agent ${item.session.agent_id.slice(0, 10)}`
              : item.session.runtime_key}
        </span>
      </span>
      <span className="trace-tree-count">{item.session.span_count}</span>
    </button>
  );

  return (
    <div className="trace-session-tree">
      {linked.map((item) => renderItem(item, "linked"))}
      {unlinked.length ? (
        <>
          <div className="trace-group-label">
            <Icon name="alert" size={12} /> 未关联会话
          </div>
          {unlinked.map((item) => renderItem(item, "unlinked"))}
        </>
      ) : null}
    </div>
  );
}

interface PhaseNavProps {
  phases: TracePhase[];
  activePhaseId: string | null;
  onSelect: (phase: TracePhase) => void;
}

function PhaseNav({ phases, activePhaseId, onSelect }: PhaseNavProps) {
  return (
    <ol className="trace-phase-nav" aria-label="关键节点">
      {phases.map((phase) => (
        <li key={phase.id}>
          <button
            type="button"
            className={`trace-phase-nav-item ${activePhaseId === phase.id ? "active" : ""}`}
            onClick={() => onSelect(phase)}
            aria-current={activePhaseId === phase.id ? "true" : undefined}
            aria-label={`跳转到 ${phase.label}，${phase.spanCount} 个 span`}
          >
            <span className={`trace-phase-nav-num ${phase.category}`}>
              {String(phase.index + 1).padStart(2, "0")}
            </span>
            <span className="trace-phase-nav-copy">
              <span className="trace-phase-nav-name">{phase.label}</span>
              <span className="trace-phase-nav-desc">{phase.description}</span>
            </span>
            <span className="trace-phase-nav-count">{phase.spanCount}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

interface TraceOverviewProps {
  model: TraceViewModel;
}

function TraceOverview({ model }: TraceOverviewProps) {
  const total = model.spans.length;
  const tickLimit = 320;
  const stride = Math.max(1, Math.ceil(total / tickLimit));
  return (
    <section className="trace-overview" aria-label="轨迹概览">
      <div className="trace-overview-head">
        <span className="trace-overview-title">轨迹概览</span>
        <span className="trace-overview-sub">
          本会话 {model.phases.length} 个阶段 · {total} 个 span · 按执行顺序
        </span>
        <div className="trace-legend" aria-hidden="true">
          <span>
            <i className="agent" />
            Agent
          </span>
          <span>
            <i className="llm" />
            模型
          </span>
          <span>
            <i className="thinking" />
            思考
          </span>
          <span>
            <i className="tool" />
            工具
          </span>
          <span>
            <i className="message" />
            消息
          </span>
        </div>
      </div>
      <div className="trace-fingerprint">
        {model.phases.map((phase) => {
          const ticks = phase.spanIds.filter(
            (_, index) => index % stride === 0,
          );
          return (
            <div
              className="trace-fp-group"
              key={phase.id}
              style={{ flexGrow: Math.max(1, phase.spanCount) }}
              title={`${phase.label} · ${phase.spanCount} spans`}
            >
              <div className="trace-fp-ticks">
                {ticks.map((spanId) => {
                  const span = model.spanById.get(spanId);
                  const kind = (span?.kind ?? "message").toLowerCase();
                  return (
                    <i
                      key={spanId}
                      className={kind}
                      title={
                        span
                          ? `${span.kind} · ${span.name ?? span.id}`
                          : undefined
                      }
                    />
                  );
                })}
              </div>
              <div className="trace-fp-label">
                <b>
                  {String(phase.index + 1).padStart(2, "0")} {phase.label}
                </b>
                <span>{phase.spanCount}</span>
              </div>
            </div>
          );
        })}
        {model.zeroDurationCount > 0 ? (
          <div className="trace-fp-note">
            <Icon name="clock" size={11} />
            {model.zeroDurationCount} / {total} 个 span 时长为 0，按顺序呈现
          </div>
        ) : null}
      </div>
    </section>
  );
}

interface DiffBlockProps {
  lines: TraceStep["diff"];
  compact?: boolean;
}

function DiffBlock({ lines, compact = false }: DiffBlockProps) {
  if (lines.length === 0) return null;
  return (
    <div
      className={`trace-diff ${compact ? "compact" : ""}`}
      aria-label="代码差异"
    >
      {lines.map((line, index) => (
        <div
          className={`trace-diff-line ${line.type}`}
          key={`${line.type}-${index}`}
        >
          <span className="trace-diff-ln">
            {line.lineNo !== null
              ? line.lineNo
              : line.type === "add"
                ? "+"
                : line.type === "del"
                  ? "−"
                  : " "}
          </span>
          <span className="trace-diff-code">{line.text || " "}</span>
        </div>
      ))}
    </div>
  );
}

interface ReadBlockProps {
  lines: TraceStep["readLines"];
}

function ReadBlock({ lines }: ReadBlockProps) {
  if (lines.length === 0) return null;
  return (
    <div className="trace-read" aria-label="文件内容预览">
      {lines.map((line, index) => (
        <div className="trace-read-line" key={`${line.lineNo}-${index}`}>
          <span className="trace-read-ln">{line.lineNo}</span>
          <span className="trace-read-code">{line.text || " "}</span>
        </div>
      ))}
    </div>
  );
}

interface ConversationViewProps {
  model: TraceViewModel;
  phases: Array<{ phase: TracePhase; steps: TraceStep[] }>;
  expandedPhases: Set<string>;
  selectedStepId: string | null;
  query: string;
  errorOnly: boolean;
  onTogglePhase: (phaseId: string) => void;
  onSelectStep: (step: TraceStep) => void;
  onClearFilters: () => void;
}

function ConversationView({
  model,
  phases,
  expandedPhases,
  selectedStepId,
  query,
  errorOnly,
  onTogglePhase,
  onSelectStep,
  onClearFilters,
}: ConversationViewProps) {
  const [taskExpanded, setTaskExpanded] = useState(false);
  const task = model.task;
  const hasFilter = Boolean(query.trim()) || errorOnly;

  return (
    <div className="trace-conversation">
      {task ? (
        <section className="trace-task-card" aria-label="用户任务">
          <div className="trace-task-top">
            <span>用户任务</span>
            <Tag color="default">{model.task?.span.kind ?? "MESSAGE"}</Tag>
          </div>
          <div className="trace-task-title">{task.title}</div>
          {task.body ? (
            taskExpanded ? (
              <pre className="trace-task-body">{task.body}</pre>
            ) : null
          ) : null}
          {task.body.length > 150 || task.body.includes("\n") ? (
            <button
              type="button"
              className="trace-task-toggle"
              aria-expanded={taskExpanded}
              onClick={() => setTaskExpanded((value) => !value)}
            >
              {taskExpanded ? "收起全文" : "展开全文"}
            </button>
          ) : null}
        </section>
      ) : null}

      {phases.length === 0 ? (
        <div className="trace-conversation-empty">
          <EmptyState
            icon="search"
            title={hasFilter ? "没有匹配的步骤" : "暂无可展示的步骤"}
            description={
              hasFilter
                ? "调整搜索词或清除「仅错误」筛选后重试。"
                : "原始 span 仍可通过右侧检查器与原始会话文件查看。"
            }
            compact
            action={
              hasFilter ? (
                <Button size="small" onClick={onClearFilters}>
                  清除筛选
                </Button>
              ) : undefined
            }
          />
        </div>
      ) : null}

      {phases.map(({ phase, steps }) => {
        const open = hasFilter || expandedPhases.has(phase.id);
        const isFix = phase.category === "fix";
        const isSummary = phase.category === "summary";
        return (
          <article
            className={`trace-turn ${open ? "open" : ""} ${isFix ? "key" : ""} ${
              isSummary ? "final" : ""
            }`}
            id={`trace-${phase.id}`}
            key={phase.id}
          >
            <button
              type="button"
              className="trace-turn-head"
              aria-expanded={open}
              aria-controls={`trace-phase-body-${phase.id}`}
              onClick={() => onTogglePhase(phase.id)}
            >
              <span className={`trace-turn-index ${phase.category}`}>
                {isSummary ? (
                  <Icon name="check" size={12} />
                ) : (
                  String(phase.index + 1).padStart(2, "0")
                )}
              </span>
              <span className="trace-turn-main">
                <span className="trace-turn-title">
                  {phase.label}
                  {isFix ? <span className="trace-turn-pill">修复</span> : null}
                  {isSummary ? (
                    <span className="trace-turn-pill final">完成</span>
                  ) : null}
                </span>
                <span className="trace-turn-summary">{phase.description}</span>
              </span>
              <span className="trace-turn-stats">
                {phase.toolCount ? `${phase.toolCount} 工具 · ` : ""}
                {phase.stepCount} 步 · {phase.spanCount} spans
                {phase.durationMs !== null
                  ? ` · ${durationLabel(phase.durationMs)}`
                  : ""}
              </span>
              <span
                className={`trace-turn-chev ${open ? "open" : ""}`}
                aria-hidden="true"
              >
                ⌄
              </span>
            </button>
            {open ? (
              <div
                className="trace-turn-body"
                id={`trace-phase-body-${phase.id}`}
              >
                {steps.map((step) =>
                  step.kind === "thinking" ? (
                    <button
                      type="button"
                      className="trace-reasoning"
                      key={step.id}
                      onClick={() => onSelectStep(step)}
                      aria-label="查看思考步骤详情"
                    >
                      <span className="trace-reasoning-label">
                        <Icon name="sparkles" size={12} />
                        思考
                      </span>
                      <span className="trace-reasoning-text">
                        {step.bodyText || step.summary}
                      </span>
                    </button>
                  ) : step.kind === "message" &&
                    phase.category === "summary" ? (
                    <button
                      type="button"
                      className="trace-final-card"
                      key={step.id}
                      onClick={() => onSelectStep(step)}
                      aria-label="查看总结消息详情"
                    >
                      <span className="trace-final-head">
                        <span className="trace-final-badge">
                          <Icon name="check" size={11} /> 总结回答
                        </span>
                        <span className="trace-final-meta">
                          {step.inputTokens !== null ||
                          step.outputTokens !== null
                            ? `${compactNumber(step.inputTokens)} in · ${compactNumber(step.outputTokens)} out`
                            : "消息"}
                        </span>
                      </span>
                      <span className="trace-final-text">
                        {step.bodyText || step.summary}
                      </span>
                    </button>
                  ) : (
                    <div
                      className={`trace-tool-card ${selectedStepId === step.id ? "selected" : ""}`}
                      key={step.id}
                    >
                      <button
                        type="button"
                        className="trace-tool-main"
                        onClick={() => onSelectStep(step)}
                        aria-label={`查看${STEP_KIND_LABEL[step.kind]}步骤详情`}
                      >
                        <span className={`trace-step-badge ${step.kind}`}>
                          {STEP_KIND_BADGE[step.kind]}
                        </span>
                        <span className="trace-tool-copy">
                          <span className="trace-tool-title">
                            <span className="trace-tool-name">
                              {step.toolName ?? STEP_KIND_LABEL[step.kind]}
                            </span>
                            <code>{step.title}</code>
                          </span>
                          <span className="trace-tool-sub">{step.summary}</span>
                        </span>
                        <span
                          className={`trace-tool-status ${stepStatusTone(step)}`}
                        >
                          {step.statusText}
                        </span>
                        {step.durationMs !== null ? (
                          <span className="trace-tool-dur">
                            {durationLabel(step.durationMs)}
                          </span>
                        ) : null}
                        <span className="trace-tool-link" aria-hidden="true">
                          查看
                          <Icon name="arrowRight" size={11} />
                        </span>
                      </button>
                      {step.diff.length ? (
                        <DiffBlock lines={step.diff.slice(0, 6)} compact />
                      ) : null}
                      {step.childSession ? (
                        <div className="trace-tool-note">
                          子会话{" "}
                          {step.childSession.agent_name ??
                            step.childSession.id.slice(0, 8)}{" "}
                          · {step.childSession.span_count} spans
                        </div>
                      ) : null}
                    </div>
                  ),
                )}
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

interface SubagentNestedProps {
  session: TraceSession;
  onOpenSession: (session: TraceSession) => void;
}

function SubagentNested({ session, onOpenSession }: SubagentNestedProps) {
  const query = useQuery({
    queryKey: ["trace-child-spans", session.id],
    queryFn: ({ signal }) =>
      fetchTraceSessionSpans(session.id, { limit: 100 }, signal),
    retry: false,
    staleTime: 30_000,
  });
  const nested = useMemo(() => {
    if (!query.data) return [];
    return buildTraceViewModel(query.data.items, [])
      .steps.filter((step) => step.kind !== "llm")
      .slice(0, 40);
  }, [query.data]);

  return (
    <div className="trace-subagent">
      <div className="trace-kv">
        <span className="trace-kv-key">子会话</span>
        <span className="trace-kv-value">
          {session.agent_name ?? session.agent_id ?? session.id.slice(0, 12)} ·{" "}
          {session.status}
        </span>
        <span className="trace-kv-key">用量</span>
        <span className="trace-kv-value">
          {session.span_count} spans · {compactNumber(session.input_tokens)} in
          · {compactNumber(session.output_tokens)} out
          {session.cost ? ` · ${formatCost(session.cost)}` : ""}
        </span>
      </div>
      {query.isLoading ? (
        <div className="trace-subagent-state">
          <Spin size="small" /> 正在加载子会话步骤…
        </div>
      ) : query.error ? (
        <div className="trace-subagent-state">
          <span>子会话步骤加载失败。</span>
          <Button size="small" onClick={() => void query.refetch()}>
            重试
          </Button>
        </div>
      ) : nested.length ? (
        <div className="trace-nested" aria-label="子 Agent 步骤">
          {nested.map((step) => (
            <div className="trace-nested-row" key={step.id}>
              <span className={`trace-step-badge tiny ${step.kind}`}>
                {STEP_KIND_BADGE[step.kind]}
              </span>
              <span className="trace-nested-tool">
                {step.toolName ?? STEP_KIND_LABEL[step.kind]}
              </span>
              <span className="trace-nested-title">{step.title}</span>
              <span className={`trace-nested-status ${stepStatusTone(step)}`}>
                {step.statusText}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div className="trace-muted-block">
          子会话没有可展示的步骤，仍可通过「打开子会话」查看原始 span。
        </div>
      )}
      <div className="trace-subagent-actions">
        <Button size="small" onClick={() => onOpenSession(session)}>
          打开子会话 <Icon name="arrowRight" size={11} />
        </Button>
        <span className="trace-muted-text">返回主轨迹由顶部会话树完成</span>
      </div>
    </div>
  );
}

interface StepBodyProps {
  step: TraceStep;
  onOpenSession: (session: TraceSession) => void;
}

function StepBody({ step, onOpenSession }: StepBodyProps) {
  const output = step.outputText;
  const inputWarning = step.inputTruncated ? (
    <div className="trace-input-warning" role="status">
      <Icon name="alert" size={12} />
      <span>
        工具输入已截断（&gt;500 字符），无法完整解析命令或 diff；完整内容需管理员打开底层
        span。
      </span>
    </div>
  ) : null;
  if (step.kind === "bash") {
    return (
      <div className="trace-step-detail">
        {inputWarning}
        <div className="trace-section-label">命令</div>
        <pre className="trace-code-block trace-command">
          <span className="trace-prompt">$</span>{" "}
          {step.command ?? step.inputText ?? "—"}
        </pre>
        <div className="trace-section-label">输出</div>
        {output ? (
          <pre className="trace-code-block trace-output">{output}</pre>
        ) : (
          <div className="trace-muted-block">没有记录工具输出。</div>
        )}
        <div className="trace-step-meta-row">
          <span className={step.error ? "error" : "ok"}>{step.statusText}</span>
          {step.exitCode !== null ? <span>退出码 {step.exitCode}</span> : null}
          {step.outputTruncated ? (
            <span>输出已截断，完整内容见底层 span</span>
          ) : null}
        </div>
      </div>
    );
  }
  if (step.kind === "edit" || step.kind === "write") {
    return (
      <div className="trace-step-detail">
        {inputWarning}
        <div className="trace-section-label">
          {step.kind === "write" ? "新文件" : "差异"}
          <span className="trace-section-hint">
            {step.diff.filter((line) => line.type === "add").length} 增加 ·{" "}
            {step.diff.filter((line) => line.type === "del").length} 删除
          </span>
        </div>
        {step.diff.length ? (
          <DiffBlock lines={step.diff} />
        ) : (
          <pre className="trace-code-block">
            {step.inputText ?? "工具参数未包含可展示的 diff。"}
          </pre>
        )}
        <div className="trace-step-meta-row">
          <span className={step.error ? "error" : "ok"}>{step.statusText}</span>
          {step.filePath ? <span className="mono">{step.filePath}</span> : null}
          <span>diff 来自工具输入；完整文件以底层 span 为准</span>
        </div>
      </div>
    );
  }
  if (step.kind === "read") {
    return (
      <div className="trace-step-detail">
        {inputWarning}
        <div className="trace-filepath">
          <Icon name="file" size={11} />
          <span className="mono">{step.filePath ?? "未知路径"}</span>
        </div>
        {step.readLines.length ? (
          <>
            <ReadBlock lines={step.readLines} />
            <div className="trace-step-meta-row">
              <span>{step.readLines.length} 行预览</span>
              <span>完整内容见工具输出或底层 span</span>
            </div>
          </>
        ) : output ? (
          <pre className="trace-code-block">{output}</pre>
        ) : (
          <div className="trace-muted-block">没有记录文件内容。</div>
        )}
      </div>
    );
  }
  if (step.kind === "skill") {
    return (
      <div className="trace-step-detail">
        <div className="trace-section-label">技能</div>
        <div className="trace-kv">
          <span className="trace-kv-key">名称</span>
          <span className="trace-kv-value">{step.title}</span>
          <span className="trace-kv-key">输入</span>
          <span className="trace-kv-value">{step.summary}</span>
        </div>
        {step.outputText ? (
          <>
            <div className="trace-section-label">技能返回</div>
            <pre className="trace-code-block">{step.outputText}</pre>
          </>
        ) : null}
        {step.childSession ? (
          <SubagentNested
            session={step.childSession}
            onOpenSession={onOpenSession}
          />
        ) : (
          <div className="trace-muted-block">
            当前轨迹没有该 skill 的内部步骤记录；保留调用参数与返回内容。
          </div>
        )}
      </div>
    );
  }
  if (step.kind === "subagent") {
    return (
      <div className="trace-step-detail">
        {step.childSession ? (
          <SubagentNested
            session={step.childSession}
            onOpenSession={onOpenSession}
          />
        ) : (
          <>
            <div className="trace-muted-block">
              未找到与本次调用关联的子会话证据；不展示虚构的嵌套步骤。
            </div>
            <div className="trace-section-label">调用参数</div>
            <pre className="trace-code-block">{formatStepInput(step)}</pre>
            {step.outputText ? (
              <>
                <div className="trace-section-label">调用返回</div>
                <pre className="trace-code-block">{step.outputText}</pre>
              </>
            ) : null}
          </>
        )}
      </div>
    );
  }
  if (step.kind === "thinking") {
    return (
      <div className="trace-step-detail">
        <div className="trace-reasoning standalone">
          <span className="trace-reasoning-label">
            <Icon name="sparkles" size={12} />
            思考
          </span>
          <span className="trace-reasoning-text">
            {step.bodyText ?? step.summary}
          </span>
        </div>
        {step.model || step.inputTokens !== null ? (
          <div className="trace-step-meta-row">
            {step.model ? <span>{step.model}</span> : null}
            {step.inputTokens !== null || step.outputTokens !== null ? (
              <span>
                {compactNumber(step.inputTokens)} in ·{" "}
                {compactNumber(step.outputTokens)} out
              </span>
            ) : null}
            {step.cost ? <span>{formatCost(step.cost)}</span> : null}
          </div>
        ) : null}
      </div>
    );
  }
  if (step.kind === "message") {
    return (
      <div className="trace-step-detail">
        <pre className="trace-message-pre">{step.bodyText ?? step.summary}</pre>
      </div>
    );
  }
  return (
    <div className="trace-step-detail">
      <div className="trace-section-label">工具输入</div>
      <pre className="trace-code-block">{formatStepInput(step)}</pre>
      {step.outputText ? (
        <>
          <div className="trace-section-label">工具返回</div>
          <pre className="trace-code-block">{step.outputText}</pre>
        </>
      ) : (
        <div className="trace-muted-block">没有记录工具返回。</div>
      )}
    </div>
  );
}

interface StepCardProps {
  step: TraceStep;
  open: boolean;
  selected: boolean;
  onToggle: () => void;
  onSelect: () => void;
  onOpenSession: (session: TraceSession) => void;
}

function StepCard({
  step,
  open,
  selected,
  onToggle,
  onSelect,
  onOpenSession,
}: StepCardProps) {
  const bodyId = `trace-step-body-${step.id}`;
  return (
    <article
      className={`trace-step ${open ? "open" : ""} ${selected ? "selected" : ""}`}
    >
      <button
        type="button"
        className="trace-step-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => {
          onToggle();
          if (!open) onSelect();
        }}
      >
        <span className={`trace-step-badge ${step.kind}`}>
          {STEP_KIND_BADGE[step.kind]}
        </span>
        <span className="trace-step-copy">
          <span className="trace-step-line">
            <span className="trace-step-tool">
              {step.toolName ?? STEP_KIND_LABEL[step.kind]}
            </span>
            <span className="trace-step-title">{step.title}</span>
          </span>
          <span className="trace-step-sub">{step.summary}</span>
        </span>
        <span className={`trace-step-status ${stepStatusTone(step)}`}>
          {step.statusText}
        </span>
        {step.durationMs !== null ? (
          <span className="trace-step-dur">
            {durationLabel(step.durationMs)}
          </span>
        ) : null}
        <span
          className={`trace-step-chev ${open ? "open" : ""}`}
          aria-hidden="true"
        >
          ⌄
        </span>
      </button>
      {open ? (
        <div className="trace-step-body" id={bodyId}>
          <StepBody step={step} onOpenSession={onOpenSession} />
        </div>
      ) : null}
    </article>
  );
}

interface StepsViewProps {
  phases: Array<{ phase: TracePhase; steps: TraceStep[] }>;
  expandedSteps: Set<string>;
  selectedStepId: string | null;
  onToggleStep: (step: TraceStep) => void;
  onSelectStep: (step: TraceStep) => void;
  onSetExpandedSteps: (ids: string[]) => void;
  onOpenSession: (session: TraceSession) => void;
}

function StepsView({
  phases,
  expandedSteps,
  selectedStepId,
  onToggleStep,
  onSelectStep,
  onSetExpandedSteps,
  onOpenSession,
}: StepsViewProps) {
  const allIds = phases.flatMap(({ steps }) => steps.map((step) => step.id));
  const totalSteps = allIds.length;
  return (
    <div className="trace-steps-view">
      <div className="trace-steps-toolbar">
        <span className="trace-steps-title">工具步骤</span>
        <span className="trace-steps-sub">
          {phases.length} 个阶段 · {totalSteps} 步 · 全部折叠
        </span>
        <span className="trace-steps-hint">
          Bash 命令/输出 · Edit diff · Read 路径/内容 · Skill/Subagent 嵌套
        </span>
        <div className="trace-steps-actions">
          <Button
            size="small"
            type="text"
            onClick={() => onSetExpandedSteps(allIds)}
            disabled={totalSteps === 0}
          >
            全部展开
          </Button>
          <Button
            size="small"
            type="text"
            onClick={() => onSetExpandedSteps([])}
            disabled={expandedSteps.size === 0}
          >
            全部折叠
          </Button>
        </div>
      </div>
      <div className="trace-steps-scroll">
        {phases.map(({ phase, steps }) => (
          <section className="trace-step-group" key={phase.id}>
            <div className="trace-step-group-head">
              <b>
                {String(phase.index + 1).padStart(2, "0")} {phase.label}
              </b>
              <span className="trace-step-group-count">
                {phase.spanCount} spans · {steps.length} 步
              </span>
            </div>
            {steps.map((step) => (
              <StepCard
                key={step.id}
                step={step}
                open={expandedSteps.has(step.id)}
                selected={selectedStepId === step.id}
                onToggle={() => onToggleStep(step)}
                onSelect={() => onSelectStep(step)}
                onOpenSession={onOpenSession}
              />
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

interface TimelineViewProps {
  timeline: TraceTimeline;
  model: TraceViewModel;
  clockComparable: boolean;
  onJumpPhase: (phase: TracePhase) => void;
}

function TimelineView({
  timeline,
  model,
  clockComparable,
  onJumpPhase,
}: TimelineViewProps) {
  const durationSegments = timeline.rows
    .filter((row) => (row.durationMs ?? 0) > 0)
    .map((row) => ({
      phase: row.phase,
      percent:
        timeline.totalMs > 0
          ? ((row.durationMs ?? 0) / timeline.totalMs) * 100
          : 0,
    }));
  const gapPercent =
    timeline.totalMs > 0 ? (timeline.gapMs / timeline.totalMs) * 100 : 0;
  const longest = [...timeline.rows]
    .filter((row) => (row.durationMs ?? 0) > 0)
    .sort((left, right) => (right.durationMs ?? 0) - (left.durationMs ?? 0))[0];
  const fixRow = timeline.rows.find((row) => row.phase.category === "fix");

  return (
    <div className="trace-timeline-view">
      <div className="trace-timeline-toolbar">
        <span className="trace-timeline-title">轮次时间线</span>
        <span className="trace-timeline-sub">
          {timeline.comparable
            ? `${clockLabel(timeline.startAt)} → ${clockLabel(timeline.endAt)} · ${durationLabel(timeline.totalMs)}`
            : "时间戳缺失或不可比 · 按阶段顺序展示"}
        </span>
        <span className="trace-timeline-mode">按轮次</span>
      </div>
      <div className="trace-timeline-body">
        {timeline.comparable ? (
          <div className="trace-timeline-ruler-row" aria-hidden="true">
            <span />
            <div className="trace-timeline-ruler">
              {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
                <span key={ratio} style={{ left: `${ratio * 100}%` }}>
                  {ratio === 0
                    ? "0"
                    : durationLabel(Math.round(timeline.totalMs * ratio))}
                </span>
              ))}
            </div>
            <span />
          </div>
        ) : null}
        <div className="trace-timeline-rows">
          {timeline.rows.map((row) => (
            <div className="trace-timeline-row" key={row.phase.id}>
              <button
                type="button"
                className="trace-timeline-label"
                onClick={() => onJumpPhase(row.phase)}
                aria-label={`跳转到 ${row.phase.label}`}
              >
                <b>{String(row.phase.index + 1).padStart(2, "0")}</b>
                <span>{row.phase.label}</span>
              </button>
              <div
                className={`trace-timeline-track ${row.ordinalOnly ? "ordinal" : ""}`}
              >
                {row.ordinalOnly ? (
                  <span
                    className="trace-timeline-ordinal"
                    style={{ left: `${row.leftPercent}%` }}
                    title="无可用时间戳"
                  />
                ) : row.zeroWidth ? (
                  <span
                    className={`trace-timeline-zero ${row.phase.category}`}
                    style={{ left: `${row.leftPercent}%` }}
                    title="0 ms（起止时间相同）"
                  />
                ) : (
                  <span
                    className={`trace-timeline-bar ${row.phase.category} ${
                      row.widthPercent < 0.5 ? "tiny" : ""
                    }`}
                    style={{
                      left: `${row.leftPercent}%`,
                      width: `${row.widthPercent}%`,
                    }}
                    title={`${row.phase.label} · ${durationLabel(row.durationMs)}`}
                  />
                )}
                {row.phase.errorCount ? (
                  <span
                    className="trace-timeline-error-mark"
                    style={{ left: `${Math.min(99, row.leftPercent)}%` }}
                    title={`${row.phase.errorCount} 个失败步骤`}
                  />
                ) : null}
              </div>
              <div className="trace-timeline-meta">
                <b>{row.phase.spanCount} spans</b>
                <span>{durationLabel(row.durationMs)}</span>
              </div>
            </div>
          ))}
        </div>
        {timeline.comparable ? (
          <section className="trace-time-summary" aria-label="时间去向">
            <div className="trace-time-summary-head">
              时间去向
              <span>本会话 {durationLabel(timeline.totalMs)}</span>
            </div>
            <div className="trace-time-stack" aria-hidden="true">
              {durationSegments.map((segment) => (
                <i
                  key={segment.phase.id}
                  className={segment.phase.category}
                  style={{ width: `${Math.max(0, segment.percent)}%` }}
                />
              ))}
              {gapPercent > 0 ? (
                <i className="gap" style={{ width: `${gapPercent}%` }} />
              ) : null}
            </div>
            <div className="trace-time-legend">
              {durationSegments.map((segment) => (
                <span key={segment.phase.id}>
                  <i className={segment.phase.category} />
                  {CATEGORY_LABEL[segment.phase.category]} {segment.phase.label}{" "}
                  <b>{durationLabel(segment.phase.durationMs)}</b>
                </span>
              ))}
              {timeline.gapMs > 0 ? (
                <span>
                  <i className="gap" />
                  阶段间隙 <b>{durationLabel(timeline.gapMs)}</b>
                </span>
              ) : null}
            </div>
          </section>
        ) : null}
        <div className="trace-timeline-note">
          <Icon name="clock" size={11} />
          <span>
            {!clockComparable
              ? "时钟不可比：当前只按阶段顺序定位，不推断并行时间关系。"
              : timeline.ordinalOnly
                ? "时间戳缺失或全部为零，时间线按阶段顺序展示，不伪造耗时。"
                : `${timeline.zeroDurationCount} / ${timeline.totalSpanCount} 个 span 的起止时间相同（0 ms）；时间线按阶段窗口聚合，span 级耗时仍在右侧详情中展示。`}
          </span>
        </div>
        {timeline.comparable && (longest || fixRow) ? (
          <div className="trace-timeline-insights">
            {longest ? (
              <div className="trace-insight">
                <span className="trace-insight-key">耗时集中</span>
                <b>{longest.phase.label}</b>
                <span>{durationLabel(longest.durationMs)}</span>
              </div>
            ) : null}
            {fixRow ? (
              <div className="trace-insight">
                <span className="trace-insight-key">修复窗口</span>
                <b>{fixRow.phase.label}</b>
                <span>{durationLabel(fixRow.durationMs)}</span>
              </div>
            ) : null}
            <div className="trace-insight">
              <span className="trace-insight-key">证据</span>
              <b>
                {model.stashCount
                  ? `git stash ${model.stashCount} 次`
                  : "无基线对比"}
              </b>
              <span>{model.zeroDurationCount} 个零宽 span</span>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

interface InspectorProps {
  step: TraceStep | null;
  span: TraceSpan | null;
  phaseLabel: string | null;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  onOpenContent: (span: TraceSpan) => void;
  openingContent: boolean;
  onClose: () => void;
  onCopyJson: (json: string) => void;
}

function InspectorOverview({
  step,
  span,
  phaseLabel,
}: {
  step: TraceStep | null;
  span: TraceSpan;
  phaseLabel: string | null;
}) {
  const facts: Array<{ key: string; value: ReactNode }> = [];
  if (step) {
    facts.push({ key: "操作", value: STEP_KIND_LABEL[step.kind] });
    facts.push({ key: "状态", value: step.statusText });
    facts.push({ key: "阶段", value: phaseLabel ?? "—" });
    facts.push({ key: "耗时", value: durationLabel(step.durationMs) });
    if (step.model) facts.push({ key: "模型", value: step.model });
    if (step.inputTokens !== null || step.outputTokens !== null) {
      facts.push({
        key: "用量",
        value: `${compactNumber(step.inputTokens)} in · ${compactNumber(step.outputTokens)} out`,
      });
    }
    if (step.cost) facts.push({ key: "成本", value: formatCost(step.cost) });
    if (step.exitCode !== null)
      facts.push({ key: "退出码", value: step.exitCode });
  } else {
    facts.push({ key: "类型", value: span.kind });
    facts.push({ key: "状态", value: span.status ?? "—" });
    facts.push({ key: "耗时", value: durationLabel(span.duration_ms) });
  }
  facts.push({
    key: "父 span",
    value: span.parent_span_id ? span.parent_span_id.slice(0, 18) : "—",
  });

  return (
    <div className="trace-inspector-scroll">
      {span.content_hidden ? (
        <Alert
          type="warning"
          showIcon
          message="完整 thinking 已按权限隐藏"
          description="只读角色只能看到推理段的类型、时长和 token；需要管理员权限才能访问完整内容。"
        />
      ) : null}
      {step ? (
        <>
          <div className="trace-inspector-lead">{step.summary}</div>
          {step.kind === "bash" ? (
            <>
              <div className="trace-detail-section-title">命令</div>
              <pre className="trace-code-block trace-command">
                <span className="trace-prompt">$</span>{" "}
                {step.command ?? step.inputText ?? "—"}
              </pre>
              <div className="trace-detail-section-title">工具返回</div>
              {step.outputText ? (
                <pre className="trace-code-block trace-output">
                  {step.outputText}
                </pre>
              ) : (
                <div className="trace-muted-block">没有记录工具输出。</div>
              )}
            </>
          ) : null}
          {step.kind === "edit" || step.kind === "write" ? (
            <>
              <div className="trace-detail-section-title">
                {step.kind === "write" ? "新文件" : "变更摘要"}
                <span className="trace-section-hint">
                  {step.diff.filter((line) => line.type === "add").length} 增加
                  · {step.diff.filter((line) => line.type === "del").length}{" "}
                  删除
                </span>
              </div>
              {step.diff.length ? (
                <DiffBlock lines={step.diff} />
              ) : (
                <pre className="trace-code-block">
                  {step.inputText ?? "没有可展示的 diff。"}
                </pre>
              )}
              <div className="trace-detail-section-title">工具返回</div>
              <div className="trace-result-box">
                <Icon name={step.error ? "alert" : "check"} size={12} />
                <span>{step.outputText ?? "没有记录工具返回。"}</span>
              </div>
            </>
          ) : null}
          {step.kind === "read" ? (
            <>
              <div className="trace-detail-section-title">文件</div>
              <div className="trace-filepath">
                <Icon name="file" size={11} />
                <span className="mono">{step.filePath ?? "未知路径"}</span>
              </div>
              <ReadBlock lines={step.readLines.slice(0, 14)} />
              {step.readLines.length > 14 ? (
                <div className="trace-muted-text">
                  仅展示前 14 行，完整内容见「输出」或底层 span。
                </div>
              ) : null}
            </>
          ) : null}
          {step.kind === "thinking" || step.kind === "message" ? (
            <>
              <div className="trace-detail-section-title">
                {step.kind === "thinking" ? "思考内容" : "消息内容"}
              </div>
              <pre className="trace-content-pre">
                {step.bodyText ?? step.summary}
              </pre>
            </>
          ) : null}
          {step.kind === "subagent" ? (
            <>
              <div className="trace-detail-section-title">子 Agent</div>
              {step.childSession ? (
                <div className="trace-kv">
                  <span className="trace-kv-key">会话</span>
                  <span className="trace-kv-value">
                    {step.childSession.agent_name ?? step.childSession.id}
                  </span>
                  <span className="trace-kv-key">规模</span>
                  <span className="trace-kv-value">
                    {step.childSession.span_count} spans ·{" "}
                    {compactNumber(step.childSession.input_tokens)} in ·{" "}
                    {compactNumber(step.childSession.output_tokens)} out
                  </span>
                </div>
              ) : (
                <div className="trace-muted-block">未找到关联子会话。</div>
              )}
            </>
          ) : null}
          {step.kind === "skill" ||
          step.kind === "llm" ||
          step.kind === "tool" ? (
            <>
              <div className="trace-detail-section-title">工具返回</div>
              {step.outputText ? (
                <pre className="trace-code-block">{step.outputText}</pre>
              ) : (
                <div className="trace-muted-block">没有记录工具返回。</div>
              )}
            </>
          ) : null}
          <div className="trace-detail-section-title">执行信息</div>
          <div className="trace-fact-grid">
            {facts.map((fact) => (
              <div className="trace-fact" key={fact.key}>
                <span className="trace-fact-key">{fact.key}</span>
                <span className="trace-fact-value">{fact.value}</span>
              </div>
            ))}
          </div>
          <div className="trace-detail-section-title">关联证据</div>
          <div className="trace-evidence-tags">
            {phaseLabel ? <Tag>{phaseLabel}</Tag> : null}
            <Tag color={step.error ? "error" : "success"}>
              {step.statusText}
            </Tag>
            {step.toolName ? <Tag>{step.toolName}</Tag> : null}
            {step.childSession ? (
              <Tag color="purple">
                子会话 {step.childSession.span_count} spans
              </Tag>
            ) : null}
          </div>
        </>
      ) : (
        <>
          <div className="trace-inspector-lead">
            {span.content_preview ?? "该 span 没有沉淀内容预览。"}
          </div>
          <div className="trace-detail-section-title">执行信息</div>
          <div className="trace-fact-grid">
            {facts.map((fact) => (
              <div className="trace-fact" key={fact.key}>
                <span className="trace-fact-key">{fact.key}</span>
                <span className="trace-fact-value">{fact.value}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {span.content_available ? (
        <div className="trace-inspector-content-action">
          <span className="trace-muted-text">
            完整正文可通过底部「打开底层 span」获取。
          </span>
        </div>
      ) : (
        <div className="trace-muted-text">
          完整正文未外置；当前仅保留脱敏预览。
        </div>
      )}
    </div>
  );
}

function Inspector({
  step,
  span,
  phaseLabel,
  loading,
  error,
  onRetry,
  onOpenContent,
  openingContent,
  onClose,
  onCopyJson,
}: InspectorProps) {
  const displaySpan = span ?? step?.span ?? null;
  const selectionKey = step?.id ?? displaySpan?.id ?? "none";
  const [tab, setTab] = useState("overview");
  useEffect(() => {
    setTab("overview");
  }, [selectionKey]);

  if (loading && !displaySpan) {
    return (
      <aside className="trace-inspector" aria-label="步骤详情">
        <Skeleton active paragraph={{ rows: 10 }} />
      </aside>
    );
  }
  if (error && !step) {
    return (
      <aside
        className="trace-inspector trace-inspector-state"
        aria-label="步骤详情"
      >
        <QueryError
          error={error}
          onRetry={onRetry}
          title="无法加载 span 详情"
          endpoint="GET /api/v1/trace-spans/:id"
        />
      </aside>
    );
  }
  if (!displaySpan) {
    return (
      <aside
        className="trace-inspector trace-inspector-state"
        aria-label="步骤详情"
      >
        <EmptyState
          icon="list"
          title="选择一个步骤"
          description="从左侧选择阶段，或在中栏的对话轨迹 / 步骤视图中选择工具、思考或消息，查看概览、输入输出、语义属性与原始记录。"
          compact
        />
      </aside>
    );
  }

  const rawJson = (() => {
    try {
      return JSON.stringify(displaySpan, null, 2) ?? "";
    } catch {
      return String(displaySpan);
    }
  })();
  const rawPreview =
    rawJson.length > 8192
      ? `${rawJson.slice(0, 8192)}\n… 已截断，完整记录请打开底层 span`
      : rawJson;
  const attributes = Object.entries(displaySpan.attributes);
  const inputText = step
    ? formatStepInput(step)
    : (displaySpan.content_preview ?? "没有内容预览。");
  const outputText = step
    ? (step.outputText ??
      step.bodyText ??
      (step.kind === "llm"
        ? "模型调用没有单独输出正文。"
        : "没有记录工具输出。"))
    : (displaySpan.content_preview ?? "没有记录输出。");
  const kindLabel = step ? STEP_KIND_LABEL[step.kind] : displaySpan.kind;
  const badge = step
    ? STEP_KIND_BADGE[step.kind]
    : displaySpan.kind.slice(0, 1);

  const overview = (
    <InspectorOverview step={step} span={displaySpan} phaseLabel={phaseLabel} />
  );
  const inputPane = (
    <div className="trace-inspector-scroll">
      <div className="trace-detail-section-title">原始输入</div>
      <pre className="trace-content-pre">{inputText}</pre>
      <div className="trace-muted-text">
        工具参数来自脱敏后的
        content_preview；超过长度上限时由后端截断，完整内容请打开底层 span。
      </div>
    </div>
  );
  const outputPane = (
    <div className="trace-inspector-scroll">
      <div className="trace-detail-section-title">原始输出</div>
      <pre className="trace-content-pre">{outputText}</pre>
      {step ? (
        <div className="trace-fact-grid">
          <div className="trace-fact">
            <span className="trace-fact-key">状态</span>
            <span className="trace-fact-value">{step.statusText}</span>
          </div>
          <div className="trace-fact">
            <span className="trace-fact-key">退出码</span>
            <span className="trace-fact-value">{step.exitCode ?? "—"}</span>
          </div>
          <div className="trace-fact">
            <span className="trace-fact-key">截断</span>
            <span className="trace-fact-value">
              {step.outputTruncated ? "是" : "否"}
            </span>
          </div>
          <div className="trace-fact">
            <span className="trace-fact-key">证据 span</span>
            <span className="trace-fact-value">{step.spans.length}</span>
          </div>
        </div>
      ) : null}
    </div>
  );
  const attributePane = (
    <div className="trace-inspector-scroll">
      {displaySpan.parent_missing ? (
        <Alert
          type="warning"
          showIcon
          message="Root fragment"
          description="父 span 不在当前 session 或已丢失，按 root fragment 展示。"
        />
      ) : null}
      <div className="trace-detail-section-title">语义属性</div>
      <div className="trace-attr-list">
        {attributes.length ? (
          attributes.map(([key, value]) => (
            <div className="trace-attr-row" key={key}>
              <span className="trace-attr-key">{key}</span>
              <span className="trace-attr-value">{textValue(value)}</span>
            </div>
          ))
        ) : (
          <span className="small muted">没有语义属性。</span>
        )}
      </div>
      <div className="trace-detail-section-title">Timing</div>
      <div className="trace-attr-list">
        <div className="trace-attr-row">
          <span className="trace-attr-key">started_at</span>
          <span className="trace-attr-value">
            {timeLabel(displaySpan.started_at)}
          </span>
        </div>
        <div className="trace-attr-row">
          <span className="trace-attr-key">ended_at</span>
          <span className="trace-attr-value">
            {timeLabel(displaySpan.ended_at)}
          </span>
        </div>
        <div className="trace-attr-row">
          <span className="trace-attr-key">duration_ms</span>
          <span className="trace-attr-value">
            {displaySpan.duration_ms ?? "—"}
          </span>
        </div>
        <div className="trace-attr-row">
          <span className="trace-attr-key">status</span>
          <span className="trace-attr-value">{displaySpan.status ?? "—"}</span>
        </div>
        <div className="trace-attr-row">
          <span className="trace-attr-key">usage</span>
          <span className="trace-attr-value">
            {compactNumber(displaySpan.input_tokens)} in ·{" "}
            {compactNumber(displaySpan.output_tokens)} out
          </span>
        </div>
      </div>
    </div>
  );
  const rawPane = (
    <div className="trace-inspector-scroll">
      <div className="trace-raw-toolbar">
        <Tag>{displaySpan.kind}</Tag>
        <Tag>JSON</Tag>
        <button
          type="button"
          className="trace-link-button"
          onClick={() => onCopyJson(rawJson)}
        >
          复制
        </button>
      </div>
      <pre className="trace-content-pre trace-raw-pre">{rawPreview}</pre>
      <Alert
        type="info"
        showIcon
        message="原件下载受管理员权限和审计控制"
        description="原始对象包含完整会话内容，不通过公开 object key 暴露。请从左侧 Raw source 获取短时地址。"
      />
    </div>
  );

  return (
    <aside className="trace-inspector" aria-label="步骤详情">
      <div className="trace-inspector-head">
        <div className="row-between">
          <span
            className={`trace-inspector-kind ${step?.kind ?? displaySpan.kind.toLowerCase()}`}
          >
            <span className="trace-kind-badge">{badge}</span>
            {kindLabel}
          </span>
          <Tooltip title="关闭详情">
            <Button
              type="text"
              size="small"
              aria-label="关闭详情"
              onClick={onClose}
            >
              <span className="trace-close-glyph" aria-hidden="true">
                ×
              </span>
            </Button>
          </Tooltip>
        </div>
        <div className="trace-inspector-title">
          {step
            ? (step.toolName ?? STEP_KIND_LABEL[step.kind])
            : (displaySpan.name ?? displaySpan.id)}
        </div>
        <div className="trace-inspector-path mono">
          {step?.filePath ?? step?.command ?? displaySpan.id}
        </div>
        <div className="trace-inspector-meta">
          <span>{phaseLabel ?? "未归入阶段"}</span>
          <span className="trace-meta-sep">·</span>
          <span className="mono">span {displaySpan.id.slice(0, 10)}</span>
          <span className="trace-meta-sep">·</span>
          <span>{clockLabel(displaySpan.started_at)}</span>
        </div>
      </div>
      <Tabs
        size="small"
        activeKey={tab}
        onChange={setTab}
        items={[
          { key: "overview", label: "概览", children: overview },
          { key: "input", label: "输入", children: inputPane },
          { key: "output", label: "输出", children: outputPane },
          { key: "attributes", label: "属性", children: attributePane },
          { key: "raw", label: "原始", children: rawPane },
        ]}
      />
      <div className="trace-inspector-foot">
        <Button
          size="small"
          icon={<Icon name="arrowRight" size={12} />}
          loading={openingContent}
          disabled={!displaySpan.content_available}
          onClick={() => onOpenContent(displaySpan)}
        >
          打开底层 span
        </Button>
        <Button
          size="small"
          icon={<Icon name="list" size={12} />}
          onClick={() => onCopyJson(rawJson)}
        >
          复制 JSON
        </Button>
      </div>
    </aside>
  );
}

export function TracePage() {
  const { message } = AntApp.useApp();
  const { session } = useAuth();
  const isAdmin = session?.role === "admin";
  const navigate = useNavigate();
  const { runId = "", caseRunId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const selection = readTraceSelection(searchParams.toString());
  const filter = readSpanFilter(searchParams.toString());
  const [signedLink, setSignedLink] = useState<
    (TraceDownloadUrl & { label: string }) | null
  >(null);
  const [centerView, setCenterView] = useState<CenterView>("conversation");
  const [searchText, setSearchText] = useState("");
  const [errorOnly, setErrorOnly] = useState(false);
  const [expandedPhases, setExpandedPhases] = useState<Set<string>>(new Set());
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [focusPhaseId, setFocusPhaseId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [dismissedSelection, setDismissedSelection] = useState(false);
  const narrow = useNarrowViewport();
  const initializedSessionRef = useRef<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);

  const updateSelection = (
    patch: Partial<{
      attempt: number | null;
      session: string | null;
      span: string | null;
      kind: string | null;
    }>,
    replace = false,
  ) => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        for (const [key, value] of Object.entries(patch)) {
          if (value === null || value === "") next.delete(key);
          else next.set(key, String(value));
        }
        return next;
      },
      { replace },
    );
  };

  const caseQuery = useQuery({
    queryKey: ["eval-case", caseRunId],
    queryFn: ({ signal }) => fetchEvaluationCase(caseRunId, signal),
    enabled: Boolean(caseRunId),
  });
  const attemptQuery = useQuery({
    queryKey: ["trace-attempt", caseRunId, selection.attempt],
    queryFn: ({ signal }) =>
      fetchAttemptTrace(caseRunId, selection.attempt, signal),
    enabled: Boolean(caseRunId),
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.traceStatus;
      return status === "pending" || status === "parsing" ? 3000 : false;
    },
  });
  const attempt = attemptQuery.data?.attempt ?? null;
  const exportSummary = useMemo(
    () =>
      summarizeTraceExport(
        attemptQuery.data?.sessions ?? [],
        attemptQuery.data?.files ?? [],
      ),
    [attemptQuery.data?.sessions, attemptQuery.data?.files],
  );
  const flat = useMemo(
    () => flattenSessionTree(attemptQuery.data?.sessions ?? []),
    [attemptQuery.data?.sessions],
  );
  const allSessions = useMemo(
    () => [...flat.linked, ...flat.unlinked].map((item) => item.session),
    [flat],
  );
  const selectedSession =
    allSessions.find((item) => item.id === selection.session) ??
    allSessions.find((item) => item.id === flat.defaultSessionId) ??
    null;
  const selectedSessionId = selectedSession?.id ?? null;

  useEffect(() => {
    if (!attemptQuery.data?.attempt) return;
    if (selection.attempt === null) {
      updateSelection({ attempt: attemptQuery.data.attempt.attempt_no }, true);
      return;
    }
    if (!selectedSessionId) return;
    if (selection.session !== selectedSessionId) {
      updateSelection({ session: selectedSessionId, span: null }, true);
    }
  }, [
    attemptQuery.data?.attempt,
    selectedSessionId,
    selection.attempt,
    selection.session,
  ]);

  const spansQuery = useInfiniteQuery({
    queryKey: [
      "trace-spans",
      caseRunId,
      attempt?.id,
      selectedSessionId,
      filter,
    ],
    queryFn: ({ pageParam, signal }) =>
      fetchTraceSessionSpans(
        selectedSessionId as string,
        {
          cursor: pageParam as string | null,
          kind: filter === "all" ? null : filter,
          limit: 100,
        },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) =>
      lastPage.pageInfo.hasMore ? lastPage.pageInfo.nextCursor : undefined,
    enabled: Boolean(selectedSessionId),
    retry: false,
    refetchInterval:
      attemptQuery.data?.traceStatus === "pending" ||
      attemptQuery.data?.traceStatus === "parsing"
        ? 3000
        : false,
  });
  const spans = useMemo(() => {
    const merged = (spansQuery.data?.pages ?? []).reduce(
      (items, page) => mergeSpanPages(items, page.items),
      [] as TraceSpan[],
    );
    return filter === "all" ? markMissingParents(merged) : merged;
  }, [filter, spansQuery.data?.pages]);
  const initialModel = useMemo(
    () => buildTraceViewModel(spans, allSessions),
    [spans, allSessions],
  );
  const visibleSpan = spans.find((item) => item.id === selection.span) ?? null;

  useEffect(() => {
    if (!selectedSessionId || selection.span !== null || dismissedSelection)
      return;
    const preferred =
      initialModel.steps.find((step) => step.category === "fix") ??
      initialModel.steps.find((step) => Boolean(step.toolName)) ??
      initialModel.steps[0] ??
      null;
    const targetId = preferred?.span.id ?? spans[0]?.id;
    if (!targetId) return;
    updateSelection({ span: targetId }, true);
  }, [dismissedSelection, initialModel, selectedSessionId, selection.span, spans]);

  const selectedSpanId = visibleSpan?.id ?? selection.span;
  const detailQuery = useQuery({
    queryKey: [
      "trace-span",
      caseRunId,
      attempt?.id,
      selectedSessionId,
      selectedSpanId,
    ],
    queryFn: ({ signal }) => fetchTraceSpan(selectedSpanId as string, signal),
    enabled: Boolean(selectedSpanId),
    retry: false,
  });
  const detailSpan = spanBelongsToSession(detailQuery.data, selectedSessionId)
    ? detailQuery.data
    : null;

  useEffect(() => {
    if (!detailQuery.data || !selectedSessionId) return;
    if (
      !spanBelongsToSession(detailQuery.data, selectedSessionId) ||
      (filter !== "all" && detailQuery.data.kind !== filter)
    ) {
      setSignedLink(null);
      updateSelection({ span: null }, true);
    }
  }, [detailQuery.data, filter, selectedSessionId]);

  const model = useMemo(() => {
    if (!detailSpan) return initialModel;
    const merged = mergeSelectedSpan(spans, detailSpan, selectedSessionId, filter);
    return buildTraceViewModel(merged, allSessions);
  }, [allSessions, detailSpan, filter, initialModel, selectedSessionId, spans]);
  const span = detailSpan ?? visibleSpan;
  const timeline = useMemo(
    () =>
      buildTraceTimeline(
        model,
        attemptQuery.data?.quality.clock_comparable ?? true,
      ),
    [attemptQuery.data?.quality.clock_comparable, model],
  );
  const selectedStep = useMemo(() => {
    if (!selectedSpanId) return null;
    const direct = model.stepBySpanId.get(selectedSpanId);
    if (direct) return direct;
    return (
      model.steps.find((step) =>
        step.related.some((item) => item.id === selectedSpanId),
      ) ?? null
    );
  }, [model, selectedSpanId]);
  const selectedPhase = selectedStep?.phaseId
    ? (model.phases.find((phase) => phase.id === selectedStep.phaseId) ?? null)
    : null;

  useEffect(() => {
    if (!selectedSessionId || model.steps.length === 0) return;
    if (initializedSessionRef.current === selectedSessionId) return;
    initializedSessionRef.current = selectedSessionId;
    const target =
      model.phases.find((phase) => phase.category === "fix") ??
      model.phases[0] ??
      null;
    setExpandedPhases(new Set(target ? [target.id] : []));
    setExpandedSteps(new Set());
  }, [model, selectedSessionId]);

  useEffect(() => {
    setExpandedPhases((previous) => {
      const valid = [...previous].filter((id) =>
        model.phases.some((phase) => phase.id === id),
      );
      return valid.length === previous.size ? previous : new Set(valid);
    });
  }, [model.phases]);

  useEffect(() => {
    if (!focusPhaseId || centerView !== "conversation") return;
    const node = document.getElementById(`trace-${focusPhaseId}`);
    node?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    setFocusPhaseId(null);
  }, [centerView, expandedPhases, focusPhaseId]);

  const filteredPhases = useMemo(() => {
    return model.phases
      .map((phase) => ({
        phase,
        steps: phase.steps.filter(
          (step) =>
            (!errorOnly || step.error) && stepMatchesQuery(step, searchText),
        ),
      }))
      .filter((item) => item.steps.length > 0);
  }, [errorOnly, model.phases, searchText]);
  const visibleStepCount = filteredPhases.reduce(
    (sum, item) => sum + item.steps.length,
    0,
  );
  const hasFilter = Boolean(searchText.trim()) || errorOnly;

  const fileDownload = useMutation({
    mutationFn: (fileId: string) => createTraceFileDownloadUrl(fileId),
    onSuccess: (result) => {
      setSignedLink({ ...result, label: "轨迹原件" });
      message.success(`原件地址已签发，${timeLabel(result.expiresAt)} 过期`);
    },
    onError: (error) =>
      message.error(
        error instanceof Error ? error.message : "原件下载地址签发失败",
      ),
  });
  const contentDownload = useMutation({
    mutationFn: (spanId: string) => createTraceSpanContentUrl(spanId),
    onSuccess: (result) => {
      setSignedLink({ ...result, label: "完整 span 内容" });
      message.success("完整内容地址已签发");
    },
    onError: (error) =>
      message.error(
        error instanceof Error ? error.message : "完整内容地址签发失败",
      ),
  });
  const archiveDownload = useMutation({
    mutationFn: (attemptNo: number) => downloadTraceArchive(caseRunId, attemptNo),
    onSuccess: (result) => {
      saveBlob(result.blob, result.filename);
      setExportOpen(false);
      message.success(`轨迹包已开始下载：${result.filename}`);
    },
    onError: (error) =>
      message.error(error instanceof Error ? error.message : "轨迹包下载失败"),
  });

  const selectAttempt = (attemptNo: number) => {
    setSignedLink(null);
    setDismissedSelection(false);
    setDetailOpen(false);
    initializedSessionRef.current = null;
    updateSelection({
      attempt: attemptNo,
      session: null,
      span: null,
      kind: null,
    });
  };
  const selectSession = (session: TraceSession) => {
    setSignedLink(null);
    setDismissedSelection(false);
    setDetailOpen(false);
    initializedSessionRef.current = null;
    updateSelection({ session: session.id, span: null, kind: null });
  };
  const selectSpan = (spanItem: TraceSpan) => {
    setSignedLink(null);
    setDismissedSelection(false);
    updateSelection({ session: spanItem.session_id, span: spanItem.id });
    if (narrow) setDetailOpen(true);
  };
  const selectStep = (step: TraceStep) => {
    selectSpan(step.span);
  };
  const openStepDetail = (step: TraceStep) => {
    selectStep(step);
    if (!narrow) {
      // Wide view keeps the always-visible inspector in sync.
      setDetailOpen(false);
    }
  };
  const changeFilter = (value: string) => {
    setSignedLink(null);
    setDismissedSelection(false);
    updateSelection({ span: null, kind: value === "all" ? null : value });
  };
  const clearFilters = () => {
    setSearchText("");
    setErrorOnly(false);
  };
  const togglePhase = (phaseId: string) => {
    setExpandedPhases((previous) => {
      const next = new Set(previous);
      if (next.has(phaseId)) next.delete(phaseId);
      else next.add(phaseId);
      return next;
    });
  };
  const toggleStep = (step: TraceStep) => {
    setExpandedSteps((previous) => {
      const next = new Set(previous);
      if (next.has(step.id)) next.delete(step.id);
      else next.add(step.id);
      return next;
    });
  };
  const jumpToPhase = (phase: TracePhase) => {
    setCenterView("conversation");
    setExpandedPhases((previous) => new Set(previous).add(phase.id));
    setFocusPhaseId(phase.id);
  };
  const clearSelection = () => {
    setDismissedSelection(true);
    setDetailOpen(false);
    setSignedLink(null);
    updateSelection({ span: null }, true);
  };
  const copyJson = (json: string) => {
    void copyText(json)
      .then(() => message.success("JSON 已复制"))
      .catch((error: unknown) =>
        message.error(error instanceof Error ? error.message : "复制失败"),
      );
  };

  if (!caseRunId) {
    return (
      <section className="page trace-page">
        <EmptyState
          icon="search"
          title="缺少用例标识"
          description="请从评测用例明细进入轨迹页。"
        />
      </section>
    );
  }
  if (attemptQuery.error) {
    return (
      <section className="page trace-page">
        <QueryError
          error={attemptQuery.error}
          onRetry={() => attemptQuery.refetch()}
          title="无法加载用例轨迹"
          endpoint="GET /api/v1/eval-case-runs/:id/trace"
        />
      </section>
    );
  }
  if (attemptQuery.isLoading || !attemptQuery.data) {
    return (
      <section className="page trace-page">
        <Skeleton active paragraph={{ rows: 2 }} />
        <div className="trace-loading-grid">
          <Skeleton active paragraph={{ rows: 12 }} />
          <Skeleton active paragraph={{ rows: 12 }} />
          <Skeleton active paragraph={{ rows: 12 }} />
        </div>
      </section>
    );
  }

  const trace = attemptQuery.data;
  const files = trace.files;
  const failedFiles = filesByStatus(files, "failed");
  const degradedFiles = filesByStatus(files, "degraded");
  const totalDuration =
    trace.attempt?.started_at && trace.attempt.ended_at
      ? Date.parse(trace.attempt.ended_at) -
        Date.parse(trace.attempt.started_at)
      : null;
  const attempts =
    caseQuery.data?.attempts ?? (trace.attempt ? [trace.attempt] : []);
  const attemptOptions = attempts.some(
    (item) => item.attempt_no === trace.attempt?.attempt_no,
  )
    ? attempts
    : trace.attempt
      ? [...attempts, trace.attempt]
      : attempts;
  const attemptTraceStatus = traceStatusForAttempt({
    caseStatus: trace.traceStatus,
    latestAttempt:
      caseQuery.data?.case.latest_attempt_no === trace.attempt?.attempt_no,
    fileStatuses: files.map((item) => item.status),
  });
  const processing =
    attemptTraceStatus === "pending" || attemptTraceStatus === "parsing";
  const noTrace = files.length === 0;
  const attemptNo = trace.attempt?.attempt_no ?? null;
  const exportPending = archiveDownload.isPending;
  const canExportArchive = isAdmin && files.length > 0 && attemptNo !== null;
  const exportTooltip = !isAdmin
    ? "仅管理员可下载轨迹包（与原始文件权限一致）"
    : files.length === 0
      ? "当前 attempt 没有可导出的原始文件"
      : attemptNo === null
        ? "轨迹尚未生成 attempt"
        : "打包下载当前 attempt 的主 Agent 与全部子 Agent 原始会话文件";
  const exportCaseKey = caseQuery.data?.case.case_key ?? trace.caseRunId;
  const exportTree = buildArchiveTree({
    caseKey: exportCaseKey,
    caseRunId: trace.caseRunId,
    attemptNo,
    main: exportSummary.main,
    subagents: exportSummary.subagents,
    unlinked: exportSummary.unlinked,
    orphanFiles: exportSummary.orphanFiles,
    unlinkedCount: exportSummary.unlinkedCount,
  });
  const sessionSpanTotal =
    selectedSession?.span_count ??
    spansQuery.data?.pages[0]?.pageInfo.total ??
    trace.stats.spanCount;
  const attemptTests =
    caseQuery.data?.attempts.find(
      (item) => item.attempt_no === attempt?.attempt_no,
    )?.test_summary ??
    caseQuery.data?.case.test_summary ??
    null;
  const totalTokens = trace.stats.inputTokens + trace.stats.outputTokens;
  const counts = (() => {
    const result: Record<string, number> = { all: model.spans.length };
    for (const item of model.spans) result[item.kind] = (result[item.kind] ?? 0) + 1;
    return result;
  })();

  const inspector = (
    <Inspector
      step={selectedStep}
      span={span ?? null}
      phaseLabel={selectedPhase?.label ?? null}
      loading={detailQuery.isLoading}
      error={detailQuery.error}
      onRetry={() => detailQuery.refetch()}
      onOpenContent={(item) => contentDownload.mutate(item.id)}
      openingContent={contentDownload.isPending}
      onClose={clearSelection}
      onCopyJson={copyJson}
    />
  );

  return (
    <section className="page trace-page">
      <div className="trace-back-row">
        <Button
          type="text"
          size="small"
          icon={<Icon name="arrowLeft" size={13} />}
          onClick={() =>
            navigate(
              `/evals/${encodeURIComponent(runId)}?tab=cases&case=${encodeURIComponent(caseRunId)}`,
            )
          }
        >
          返回评测详情
        </Button>
      </div>
      <PageHeading
        eyebrow={`Session trace · Attempt ${attempt?.attempt_no ?? "—"}`}
        title={caseQuery.data?.case.case_key ?? attemptQuery.data.caseRunId}
        description={`${attempt?.status ?? "unknown"} · ${trace.parserVersions.join(", ") || "parser pending"} · 主 Agent 与子 Agent 使用统一父子证据展示`}
        actions={
          <>
            <Select
              value={attempt?.attempt_no}
              style={{ width: 190 }}
              aria-label="选择 attempt"
              options={attemptOptions.map((item) => ({
                value: item.attempt_no,
                label: `Attempt ${item.attempt_no} · ${item.status}`,
              }))}
              onChange={selectAttempt}
            />
            <Tooltip title={exportTooltip}>
              <span className="trace-export-action">
                <Button
                  type="primary"
                  icon={<DownloadGlyph />}
                  disabled={!canExportArchive || exportPending}
                  loading={exportPending}
                  onClick={() => setExportOpen(true)}
                >
                  下载轨迹包
                </Button>
              </span>
            </Tooltip>
          </>
        }
      />

      {attempt?.error_summary ? (
        <Alert
          type="error"
          showIcon
          className="trace-quality-banner"
          message={attempt.error_code ?? "Attempt 执行失败"}
          description={attempt.error_summary}
        />
      ) : null}
      {noTrace && attempt?.status === "failed" ? (
        <Alert
          type="warning"
          showIcon
          className="trace-quality-banner"
          message="该 attempt 没有可用轨迹"
          description="执行失败时可能尚未生成原生会话文件；测试结果和失败原因仍以用例详情为准。"
        />
      ) : processing ? (
        <Alert
          type="info"
          showIcon
          className="trace-quality-banner"
          message="轨迹仍在处理中"
          description="评分和测试结果不受影响；导入完成后本页会自动刷新。"
        />
      ) : null}
      {failedFiles.length || degradedFiles.length ? (
        <Alert
          type={failedFiles.length ? "error" : "warning"}
          showIcon
          className="trace-quality-banner"
          message={
            failedFiles.length
              ? `${failedFiles.length} 个轨迹文件解析失败`
              : `${degradedFiles.length} 个轨迹文件已降级`
          }
          description={trace.quality.issues
            .slice(0, 2)
            .map(
              (item) =>
                item.error_summary ?? item.error_code ?? item.logical_path,
            )
            .filter(Boolean)
            .join(" · ")}
        />
      ) : null}
      {!trace.quality.clock_comparable ? (
        <Alert
          type="warning"
          showIcon
          className="trace-quality-banner"
          message="时间不可比"
          description="部分 span 起止时间倒置或缺失，时间线已退回按阶段顺序展示，不推断并行时间关系。"
        />
      ) : null}

      <TraceOverview model={model} />

      <section className="trace-shell">
        <div className="trace-top">
          <Tag color="purple">Claude Code</Tag>
          <Tag color="blue">
            {selectedSession?.parser_version ?? "parser pending"}
          </Tag>
          {statusTag(attemptTraceStatus)}
          <span className="small muted mono">
            {attempt?.id ?? "尚未生成 attempt"}
          </span>
          <div className="trace-summary" aria-label="轨迹摘要">
            <span>
              <strong>{durationLabel(totalDuration)}</strong> 总耗时
            </span>
            <span>
              <strong>{compactNumber(totalTokens)}</strong> tokens
            </span>
            <span>
              <strong>{formatCost(model.cost ?? selectedSession?.cost)}</strong> 成本
            </span>
            <span>
              <strong>{sessionSpanTotal || trace.stats.spanCount}</strong> spans
            </span>
            <span
              className={attemptTests?.failed ? "trace-summary-danger" : ""}
            >
              <strong>{testSummaryLabel(attemptTests)}</strong> 测试
            </span>
          </div>
        </div>

        <div className="trace-mobile-session-picker">
          <Select
            value={selectedSessionId ?? undefined}
            options={allSessions.map((item) => ({
              value: item.id,
              label: `${item.agent_name ?? item.id} · ${item.span_count} spans`,
            }))}
            onChange={(value) => {
              const next = allSessions.find((item) => item.id === value);
              if (next) selectSession(next);
            }}
            style={{ width: "100%" }}
            aria-label="选择会话"
          />
        </div>

        <div className="trace-layout">
          <aside className="trace-tree-pane">
            <div className="trace-pane-section-head">
              关键节点
              <span>{model.phases.length}</span>
            </div>
            <PhaseNav
              phases={model.phases}
              activePhaseId={selectedStep?.phaseId ?? null}
              onSelect={jumpToPhase}
            />
            <div className="trace-pane-section-head">
              会话
              <span>{allSessions.length}</span>
            </div>
            <SessionList
              linked={flat.linked}
              unlinked={flat.unlinked}
              selectedId={selectedSessionId}
              onSelect={selectSession}
            />
            <div className="trace-raw-source">
              <div className="trace-pane-section-head">原始文件</div>
              <div className="trace-raw-summary small muted">
                主 Agent {exportSummary.mainAgentCount} · 子 Agent {exportSummary.subagentCount} · {files.length} 个原始文件
              </div>
              {files.length ? (
                files.map((file) => (
                  <button
                    key={file.id}
                    type="button"
                    className="trace-file-item"
                    onClick={() => fileDownload.mutate(file.id)}
                    disabled={fileDownload.isPending}
                  >
                    <Icon name="file" size={14} />
                    <span className="truncate mono">
                      {file.logical_path ?? file.id}
                    </span>
                    {statusTag(file.status)}
                  </button>
                ))
              ) : (
                <span className="small muted">没有登记的原生文件。</span>
              )}
              <Tooltip title={exportTooltip}>
                <span className="trace-export-action">
                  <Button
                    className="trace-export-raw-button"
                    size="small"
                    block
                    icon={<DownloadGlyph size={12} />}
                    disabled={!canExportArchive || exportPending}
                    loading={exportPending}
                    onClick={() => setExportOpen(true)}
                  >
                    打包下载
                  </Button>
                </span>
              </Tooltip>
            </div>
            <div className="trace-outline-foot">
              <Icon name="list" size={11} />主 Agent 与子 Agent 使用统一父子证据
            </div>
          </aside>

          <section className="trace-center" aria-label="轨迹主视图">
            <div className="trace-center-toolbar">
              <div
                className="trace-view-tabs"
                role="tablist"
                aria-label="轨迹视图"
              >
                {CENTER_VIEWS.map((view) => (
                  <button
                    key={view.value}
                    type="button"
                    role="tab"
                    aria-selected={centerView === view.value}
                    className={`trace-view-tab ${centerView === view.value ? "active" : ""}`}
                    onClick={() => setCenterView(view.value)}
                  >
                    {view.label}
                  </button>
                ))}
              </div>
              <div className="trace-view-tools">
                <Input
                  allowClear
                  size="small"
                  className="trace-search"
                  prefix={<Icon name="search" size={13} />}
                  placeholder="搜索步骤、工具或输出"
                  aria-label="搜索步骤、工具或输出"
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                />
                <Button
                  size="small"
                  className={`trace-error-filter ${errorOnly ? "active" : ""}`}
                  aria-pressed={errorOnly}
                  onClick={() => setErrorOnly((value) => !value)}
                >
                  仅错误 {model.errorCount}
                </Button>
                <Button
                  size="small"
                  className="trace-detail-trigger"
                  onClick={() => setDetailOpen(true)}
                  aria-label="打开详情面板"
                >
                  详情
                </Button>
              </div>
            </div>
            <div className="trace-filterbar">
              <Segmented
                size="small"
                value={filter}
                options={FILTERS.map((item) => ({
                  value: item.value,
                  label: (
                    <span>
                      {item.label}
                      <span className="trace-filter-count">
                        {item.value === "all"
                          ? counts.all
                          : (counts[item.value] ?? 0)}
                      </span>
                    </span>
                  ),
                }))}
                onChange={(value) => changeFilter(String(value))}
              />
              <span className="trace-visible-count" aria-live="polite">
                {visibleStepCount} / {model.steps.length} 步
              </span>
              {hasFilter ? (
                <Button
                  type="link"
                  size="small"
                  className="trace-clear-filters"
                  onClick={clearFilters}
                >
                  清除
                </Button>
              ) : null}
            </div>

            {spansQuery.error ? (
              <div className="trace-center-state">
                <QueryError
                  error={spansQuery.error}
                  onRetry={() => spansQuery.refetch()}
                  title="无法加载 session spans"
                  endpoint="GET /api/v1/trace-sessions/:id/spans"
                />
              </div>
            ) : spansQuery.isLoading ? (
              <div className="trace-center-state">
                <Spin tip="正在加载 session spans">
                  <div className="trace-spin-space" />
                </Spin>
              </div>
            ) : centerView === "conversation" ? (
              <ConversationView
                model={model}
                phases={filteredPhases}
                expandedPhases={expandedPhases}
                selectedStepId={selectedStep?.id ?? null}
                query={searchText}
                errorOnly={errorOnly}
                onTogglePhase={togglePhase}
                onSelectStep={openStepDetail}
                onClearFilters={clearFilters}
              />
            ) : centerView === "steps" ? (
              <StepsView
                phases={filteredPhases}
                expandedSteps={expandedSteps}
                selectedStepId={selectedStep?.id ?? null}
                onToggleStep={toggleStep}
                onSelectStep={openStepDetail}
                onSetExpandedSteps={(ids) => setExpandedSteps(new Set(ids))}
                onOpenSession={selectSession}
              />
            ) : (
              <TimelineView
                timeline={timeline}
                model={model}
                clockComparable={trace.quality.clock_comparable}
                onJumpPhase={jumpToPhase}
              />
            )}

            {spansQuery.hasNextPage ? (
              <div className="trace-load-more">
                <Button
                  size="small"
                  loading={spansQuery.isFetchingNextPage}
                  onClick={() => void spansQuery.fetchNextPage()}
                >
                  加载更多 span
                </Button>
              </div>
            ) : null}
          </section>

          {inspector}
        </div>
      </section>

      <Drawer
        title="步骤详情"
        placement="right"
        width={Math.min(
          420,
          typeof window !== "undefined" ? window.innerWidth : 420,
        )}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        className="trace-detail-drawer"
        styles={{ body: { padding: 0 } }}
      >
        {detailOpen ? inspector : null}
      </Drawer>

      <Modal
        open={Boolean(signedLink)}
        title="受控地址已签发"
        onCancel={() => setSignedLink(null)}
        footer={[
          <Button key="close" onClick={() => setSignedLink(null)}>
            关闭
          </Button>,
          <Button
            key="open"
            type="primary"
            href={signedLink?.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            打开{signedLink?.label ?? "文件"}
          </Button>,
        ]}
      >
        <Alert
          type="info"
          showIcon
          message="短时地址不会进入应用 URL 或持久缓存"
          description={
            signedLink
              ? `该地址将在 ${timeLabel(signedLink.expiresAt)} 过期；下载行为已按管理员权限审核。`
              : ""
          }
        />
      </Modal>
      <Modal
        open={exportOpen}
        title="下载轨迹包"
        width={760}
        className="trace-export-modal"
        maskClosable={!exportPending}
        closable={!exportPending}
        onCancel={() => {
          if (!exportPending) setExportOpen(false);
        }}
        footer={[
          <Button
            key="cancel"
            disabled={exportPending}
            onClick={() => setExportOpen(false)}
          >
            取消
          </Button>,
          <Button
            key="download"
            type="primary"
            icon={<DownloadGlyph size={13} />}
            loading={exportPending}
            disabled={!canExportArchive}
            onClick={() => {
              if (attemptNo !== null) archiveDownload.mutate(attemptNo);
            }}
          >
            下载 ZIP · {formatBytes(exportSummary.estimatedArchiveSize)}
          </Button>,
        ]}
      >
        <div className="trace-export-subtitle">
          当前 attempt · Attempt {attemptNo ?? "—"} · {trace.attempt?.status ?? "unknown"} ·{" "}
          <span className="mono">{exportCaseKey}</span>
        </div>
        <Alert
          type="info"
          showIcon
          className="trace-export-banner"
          message={
            <span>
              <strong>主 Agent 与全部子 Agent 打包下载。</strong>
              原始会话文件会打进同一个 ZIP，不重新序列化，保留原生 JSONL；未关联会话会在清单中标记。
            </span>
          }
        />
        <div className="trace-export-stats">
          <div className="trace-export-stat">
            <strong>{exportSummary.mainAgentCount}</strong>
            <span>主 Agent</span>
          </div>
          <div className="trace-export-stat">
            <strong>{exportSummary.subagentCount}</strong>
            <span>子 Agent</span>
          </div>
          <div className="trace-export-stat">
            <strong>{exportSummary.rawFileCount}</strong>
            <span>原始文件</span>
          </div>
          <div className="trace-export-stat">
            <strong>{formatBytes(exportSummary.estimatedArchiveSize)}</strong>
            <span>ZIP 预估</span>
          </div>
        </div>
        <div className="trace-export-hint">
          ZIP 预估按原始文件大小与清单开销估算，实际以下载结果为准。
        </div>
        <div className="trace-export-section">
          <div className="trace-export-section-head">
            包含的会话<span className="muted">{exportSummary.sessionCount}</span>
          </div>
          {exportSummary.sessions.length ? (
            <div className="trace-export-session-list">
              {exportSummary.sessions.map((row) => (
                <div className="trace-export-session" key={row.id}>
                  <span
                    className={`trace-export-mark ${row.role === "unlinked" ? "warn" : ""}`}
                  >
                    {row.role === "unlinked" ? "U" : "A"}
                  </span>
                  <div className="trace-export-session-main">
                    <div className="trace-export-session-name">{row.name}</div>
                    <div className="trace-export-session-meta">
                      {row.roleLabel} · {row.spanCount} spans ·{" "}
                      <span className="mono">{row.nativeSessionId}</span>
                    </div>
                  </div>
                  <Tag
                    color={
                      row.role === "main"
                        ? "purple"
                        : row.role === "subagent"
                          ? "blue"
                          : "warning"
                    }
                  >
                    {row.roleLabel}
                  </Tag>
                  <span className="trace-export-session-size">
                    {row.fileCount
                      ? `${row.fileCount} 个文件 · ${formatBytes(row.fileSize)}`
                      : `${row.spanCount} spans`}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="trace-export-empty">
              该 attempt 还没有登记会话，原始文件仍会按清单归档。
            </div>
          )}
          {exportSummary.unlinkedCount ? (
            <div className="trace-export-hint">
              {exportSummary.unlinkedCount} 个未关联会话会写入 unlinked/，并在清单中标注。
            </div>
          ) : null}
          <div className="trace-export-hint">
            从子 Agent 会话发起下载时，仍会导出它所属的完整会话组，避免只拿到半条轨迹。
          </div>
        </div>
        <div className="trace-export-section">
          <div className="trace-export-section-head">
            ZIP 目录<span className="muted">原始 JSONL + 清单</span>
          </div>
          <pre className="trace-export-tree">{exportTree.join("\n")}</pre>
          <div className="trace-export-hint">
            manifest.json 记录 case、attempt、parser 版本、session / span 数量与每个文件的
            sha256；文件缺失或会话未关联时会在清单中标注，不生成坏包。
          </div>
        </div>
      </Modal>
    </section>
  );
}

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Drawer,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Skeleton,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  TERMINAL_EVAL_STATUSES,
  cancelEvaluation,
  fetchCaseTests,
  fetchEvaluationDetail,
  fetchEvaluationEvents,
  fetchEvaluationResults,
  resumeEvaluationBudget,
  type CaseTestResult,
  type EvaluationDetail,
  type EvaluationEvent,
  type MetricResult,
} from "../api/evaluations";
import {
  EMPTY_CASE_COUNTS,
  fetchEvaluationCase,
  fetchEvaluationCases,
  fetchEvaluationConfig,
  retryEvaluationCase,
  retryFailedEvaluationCases,
  type CaseTestSummary,
  type EvalCaseCounts,
  type EvalCaseDetail,
  type EvalCaseRow,
  type EvalConfigSnapshot,
} from "../api/evalWorkbench";
import { useEvalEvents, mergeEvents, type EvalEventsState } from "../api/useEvalEvents";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { StatusCard } from "../components/StatusCard";
import { formatMoney } from "../utils/money";

const TAB_KEYS = ["overview", "cases", "results", "config", "events"] as const;
type TabKey = (typeof TAB_KEYS)[number];

const CASE_STATUS_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "resolved", label: "已解决" },
  { value: "unresolved", label: "未解决" },
  { value: "running", label: "运行中" },
  { value: "failed", label: "失败" },
  { value: "queued", label: "排队" },
] as const;

const EXECUTION_OPTIONS = [
  { value: "queued", label: "排队" },
  { value: "running", label: "运行中" },
  { value: "succeeded", label: "成功" },
  { value: "failed", label: "失败" },
  { value: "timeout", label: "超时" },
  { value: "cancelled", label: "已取消" },
];

const RESULT_OPTIONS = [
  { value: "resolved", label: "已解决" },
  { value: "unresolved", label: "未解决" },
  { value: "not_scored", label: "未评分" },
];

const TRACE_OPTIONS = [
  { value: "pending", label: "待处理" },
  { value: "ready", label: "可用" },
  { value: "failed", label: "失败" },
  { value: "unlinked", label: "未关联" },
];

const runStatusMeta: Record<string, { label: string; color: string }> = {
  preparing: { label: "准备中", color: "default" },
  queued: { label: "排队中", color: "default" },
  running: { label: "运行中", color: "processing" },
  finalizing: { label: "收口中", color: "processing" },
  completed: { label: "已完成", color: "success" },
  partial: { label: "部分成功", color: "warning" },
  failed: { label: "失败", color: "error" },
  cancelling: { label: "取消中", color: "warning" },
  cancelled: { label: "已取消", color: "default" },
  pausing_budget: { label: "预算暂停中", color: "warning" },
  paused_budget: { label: "预算已暂停", color: "warning" },
};

const executionStatusMeta: Record<string, { label: string; color: string }> = {
  queued: { label: "排队", color: "default" },
  running: { label: "运行中", color: "processing" },
  succeeded: { label: "成功", color: "success" },
  failed: { label: "失败", color: "error" },
  timeout: { label: "超时", color: "warning" },
  cancelled: { label: "已取消", color: "default" },
};

const resultStatusMeta: Record<string, { label: string; color: string }> = {
  resolved: { label: "已解决", color: "success" },
  unresolved: { label: "未解决", color: "warning" },
  not_scored: { label: "未评分", color: "default" },
};

const traceStatusMeta: Record<string, { label: string; color: string }> = {
  pending: { label: "待处理", color: "default" },
  ready: { label: "可用", color: "success" },
  failed: { label: "解析失败", color: "error" },
  unlinked: { label: "未关联", color: "warning" },
};

const attemptStatusMeta: Record<string, { label: string; color: string }> = {
  pending: { label: "排队", color: "default" },
  dispatching: { label: "派发中", color: "processing" },
  queued: { label: "已排队", color: "default" },
  leased: { label: "已租用", color: "processing" },
  running: { label: "运行中", color: "processing" },
  scoring: { label: "评分中", color: "processing" },
  uploading: { label: "上传中", color: "processing" },
  succeeded: { label: "成功", color: "success" },
  failed: { label: "失败", color: "error" },
  timeout: { label: "超时", color: "warning" },
  cancelled: { label: "已取消", color: "default" },
  artifact_missing: { label: "产物缺失", color: "error" },
  dispatch_failed: { label: "派发失败", color: "error" },
};

const eventTypeLabels: Record<string, string> = {
  "eval.created": "评测已创建",
  "eval.dispatched": "分片已派发",
  "eval.dispatch_failed": "分片派发失败",
  "eval.cancelling": "取消处理中",
  "eval.cancelled": "评测已取消",
  "eval.budget_pausing": "预算用尽，暂停收口",
  "eval.budget_paused": "进入预算暂停",
  "eval.budget_resumed": "增加预算并恢复",
  "eval.retry_failed": "批量重试失败用例",
  "case.started": "用例开始执行",
  "case.heartbeat": "用例心跳",
  "case.uploaded": "对象上传清单",
  "case.completed": "用例完成",
  "case.failed": "用例失败",
  "case.timeout": "用例超时",
  "case.retry_queued": "自动重试已排队",
  "case.retry_requested": "手动重试已排队",
  "case.artifact_missing": "结果对象缺失",
  "case.started_late": "迟到 started（保留审计）",
  "case.started_rejected": "启动被拒绝",
};

const retryBlockedLabels: Record<string, string> = {
  run_not_running: "评测不在运行中，不能重试用例",
  case_not_failed: "只有失败或超时的用例可以重试",
  attempts_exhausted: "该用例已达到最大 attempts 次数",
  attempt_missing: "该用例没有可追溯的 attempt",
  attempt_active: "该用例仍有进行中的 attempt",
};

function statusTag(
  status: string,
  meta: Record<string, { label: string; color: string }>,
) {
  const entry = meta[status] ?? { label: status, color: "default" };
  return <Tag color={entry.color}>{entry.label}</Tag>;
}

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function shortDigest(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/^sha256:/, "").slice(0, 12);
}

function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.round(seconds / 60)} 分钟`;
}

function formatMetricValue(metric: MetricResult): string {
  const value = metric.value;
  if (value === null || value === undefined) return "N/A";
  if (metric.display_kind === "percentage" && typeof value === "number") {
    return `${(value * 100).toFixed(1)}%`;
  }
  if (metric.display_kind === "number" && typeof value === "number") {
    return value.toFixed(2).replace(/\.00$/, "");
  }
  if (metric.display_kind === "ms" && typeof value === "number") {
    return formatDuration(value);
  }
  if (metric.display_kind === "cny" && typeof value === "number") {
    return formatMoney(value);
  }
  if (typeof value === "object") return JSON.stringify(value);
  return metric.unit ? `${String(value)} ${metric.unit}` : String(value);
}

function formatDecimal(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return Number.isInteger(parsed) ? String(parsed) : parsed.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

const denominatorPolicyLabels: Record<string, string> = {
  completed_cases: "已完成用例",
  all_cases: "全部用例",
  scored_cases: "已评分用例",
  terminal_cases: "终态用例",
};

const directionLabels: Record<string, string> = {
  higher_is_better: "越高越好",
  lower_is_better: "越低越好",
};

const displayKindLabels: Record<string, string> = {
  number: "数值",
  percentage: "百分比",
  ms: "耗时",
  cny: "人民币",
  histogram: "直方图",
};

function denominatorPolicyLabel(value: string | null | undefined): string {
  if (!value) return "未声明";
  return denominatorPolicyLabels[value] ?? value;
}

function directionLabel(value: string): string {
  return directionLabels[value] ?? value;
}

function isKnownMetricKind(kind: string): boolean {
  return ["number", "percentage", "ms", "cny"].includes(kind);
}

function SummaryList({ items }: { items: Array<[string, React.ReactNode]> }) {
  return (
    <div className="summary-list eval-summary-list">
      {items.map(([label, value]) => (
        <div className="summary-row" key={label}>
          <span className="summary-label">{label}</span>
          <span className="summary-value">{value === null || value === undefined || value === "" ? "—" : value}</span>
        </div>
      ))}
    </div>
  );
}

function StackedProgress({ counts, total }: { counts: EvalCaseCounts; total: number }) {
  const segments = [
    { key: "resolved", label: "已解决", tone: "success", value: counts.resolved },
    { key: "unresolved", label: "未解决", tone: "neutral", value: counts.unresolved },
    { key: "running", label: "运行中", tone: "running", value: counts.running },
    { key: "failed", label: "失败 / 超时", tone: "danger", value: counts.failed },
    { key: "queued", label: "排队", tone: "queued", value: counts.queued },
  ];
  const safeTotal = Math.max(1, total);
  return (
    <div className="stacked-progress">
      <div
        className="stacked-bar"
        role="img"
        aria-label={segments.map((segment) => `${segment.label} ${segment.value}`).join("，")}
      >
        {segments.map((segment) =>
          segment.value > 0 ? (
            <span
              key={segment.key}
              className={`stacked-segment ${segment.tone}`}
              style={{ width: `${(segment.value / safeTotal) * 100}%` }}
              title={`${segment.label} ${segment.value}`}
            />
          ) : null,
        )}
      </div>
      <div className="stacked-legend">
        {segments.map((segment) => (
          <span className="stacked-legend-item" key={segment.key}>
            <span className={`legend-swatch ${segment.tone}`} />
            {segment.label} <strong>{segment.value}</strong>
          </span>
        ))}
      </div>
    </div>
  );
}

function relativeSync(value: string | null): string {
  if (!value) return "";
  const parsed = new Date(value).getTime();
  if (Number.isNaN(parsed)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
  if (seconds < 60) return `${seconds} 秒前同步`;
  return `${Math.floor(seconds / 60)} 分钟前同步`;
}

function LiveBadge({
  state,
  terminal,
}: {
  state: EvalEventsState;
  terminal: boolean;
}) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((value) => value + 1), 5000);
    return () => clearInterval(timer);
  }, []);
  if (terminal) {
    return <span className="eval-live final"><Icon name="check" size={12} /> 事件流已结束</span>;
  }
  if (state.connected) {
    return (
      <span className="eval-live">
        <span className="status-dot pulse" /> 实时连接
        {state.lastSyncedAt ? ` · ${relativeSync(state.lastSyncedAt)}` : ""}
      </span>
    );
  }
  return <span className="eval-live idle">等待事件流连接…</span>;
}

function TestSummaryChips({ summary }: { summary: CaseTestSummary }) {
  const total = summary.passed + summary.failed + summary.skipped + summary.error;
  if (!total) return <span className="muted small">暂无测试打点</span>;
  return (
    <Space size={4} wrap>
      <Tag color="success">通过 {summary.passed}</Tag>
      {summary.failed ? <Tag color="error">失败 {summary.failed}</Tag> : null}
      {summary.skipped ? <Tag>跳过 {summary.skipped}</Tag> : null}
      {summary.error ? <Tag color="warning">错误 {summary.error}</Tag> : null}
      {summary.f2p_failed ? <Tag color="error">F2P 失败 {summary.f2p_failed}</Tag> : null}
      {summary.p2p_failed ? <Tag color="error">P2P 失败 {summary.p2p_failed}</Tag> : null}
    </Space>
  );
}

function attemptsSummary(attempts: EvalCaseDetail["attempts"], attemptNo: number | null) {
  return attempts.find((attempt) => attempt.attempt_no === attemptNo)?.test_summary ?? null;
}

function eventSummary(event: EvaluationEvent, caseKeys: Map<string, string>): string {
  const payload = event.payload;
  const caseKey = event.case_run_id ? caseKeys.get(event.case_run_id) : null;
  const payloadCaseKey = typeof payload.case_key === "string" ? payload.case_key : null;
  const subject = payloadCaseKey ?? caseKey ?? event.case_run_id ?? "评测";
  switch (event.event_type) {
    case "eval.created":
      return `创建 ${payload.case_count ?? "?"} 个用例，${payload.shard_count ?? "?"} 个分片`;
    case "eval.dispatched":
      return `分片 ${payload.shard_id ?? "—"} 派发成功`;
    case "eval.dispatch_failed":
      return `分片 ${payload.shard_id ?? "—"} 派发失败：${payload.last_error ?? "未知原因"}`;
    case "eval.cancelling":
    case "eval.cancelled":
      return `取消原因：${payload.reason ?? "client_requested"}`;
    case "eval.budget_paused":
      return `已花费 ${formatMoney(payload.spent_cny as string | number | undefined)}，等待增加预算`;
    case "eval.budget_resumed":
      return `增加 ${formatMoney(payload.additional_budget_cny as string | number | undefined)} 预算，恢复 ${payload.attempts_created ?? "?"} 个用例`;
    case "eval.retry_failed":
      return `批量重试 ${payload.retried ?? 0} 个用例，跳过 ${payload.skipped ?? 0} 个`;
    case "case.retry_requested":
      return `${subject} attempt ${payload.previous_attempt_no ?? "?"} → ${event.attempt_no ?? "?"}，操作人 ${payload.requested_by ?? "—"}`;
    case "case.retry_queued":
      return `${subject} 自动重试 attempt ${payload.new_attempt_no ?? event.attempt_no ?? "?"}`;
    case "case.failed":
    case "case.timeout":
      return `${subject} ${payload.error_code ?? event.event_type}：${payload.error_summary ?? "无摘要"}`;
    case "case.completed":
      return `${subject} 执行完成`;
    case "case.artifact_missing":
      return `${subject} 结果对象缺失`;
    default: {
      const keys = Object.keys(payload);
      return keys.length ? `${keys.slice(0, 3).map((key) => `${key}=${String(payload[key])}`).join(" · ")}` : "—";
    }
  }
}

export function EvalDetailPage() {
  const { message } = AntApp.useApp();
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab") ?? "overview";
  const tab: TabKey = (TAB_KEYS as readonly string[]).includes(tabParam)
    ? (tabParam as TabKey)
    : "overview";
  const status = searchParams.get("status") ?? "all";
  const q = searchParams.get("q") ?? "";
  const executionFilter = searchParams.get("execution_status") ?? "";
  const resultFilter = searchParams.get("result_status") ?? "";
  const traceFilter = searchParams.get("trace_status") ?? "";
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const pageSizeRaw = Number(searchParams.get("pageSize") ?? "20");
  const pageSize = [20, 50, 100].includes(pageSizeRaw) ? pageSizeRaw : 20;
  const selectedCaseId = searchParams.get("case");
  const attemptRaw = Number(searchParams.get("attempt") ?? "");
  const urlAttempt = Number.isFinite(attemptRaw) && attemptRaw > 0 ? attemptRaw : null;

  const [searchDraft, setSearchDraft] = useState(q);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [bulkRetryOpen, setBulkRetryOpen] = useState(false);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [additionalBudget, setAdditionalBudget] = useState(10);

  const updateParams = (patch: Record<string, string | null>) => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        for (const [key, value] of Object.entries(patch)) {
          if (value === null || value === "") next.delete(key);
          else next.set(key, value);
        }
        return next;
      },
      { replace: true },
    );
  };

  const detail = useQuery({
    queryKey: ["evaluation", runId],
    queryFn: ({ signal }) => fetchEvaluationDetail(runId, signal),
    enabled: Boolean(runId),
  });
  const results = useQuery({
    queryKey: ["evaluation", runId, "results"],
    queryFn: ({ signal }) => fetchEvaluationResults(runId, signal),
    enabled: Boolean(runId) && tab === "results",
  });
  const config = useQuery({
    queryKey: ["evaluation", runId, "config"],
    queryFn: ({ signal }) => fetchEvaluationConfig(runId, signal),
    enabled: Boolean(runId),
  });
  const cases = useQuery({
    queryKey: ["evaluation", runId, "cases", { status, q, executionFilter, resultFilter, traceFilter, page, pageSize }],
    queryFn: ({ signal }) =>
      fetchEvaluationCases(
        runId,
        {
          status,
          q,
          execution_status: executionFilter || undefined,
          result_status: resultFilter || undefined,
          trace_status: traceFilter || undefined,
          limit: pageSize,
          offset: (page - 1) * pageSize,
        },
        signal,
      ),
    enabled: Boolean(runId) && tab === "cases",
  });
  const caseDetail = useQuery({
    queryKey: ["eval-case", selectedCaseId],
    queryFn: ({ signal }) => fetchEvaluationCase(selectedCaseId as string, signal),
    enabled: Boolean(selectedCaseId),
  });
  const selectedAttempt = urlAttempt ?? caseDetail.data?.tests_attempt ?? null;
  const tests = useQuery({
    queryKey: ["eval-case", selectedCaseId, "tests", selectedAttempt],
    queryFn: ({ signal }) =>
      fetchCaseTests(selectedCaseId as string, selectedAttempt, signal),
    enabled: Boolean(selectedCaseId) && selectedAttempt !== null,
  });

  const terminal = TERMINAL_EVAL_STATUSES.includes(detail.data?.status ?? "");
  const eventState = useEvalEvents(runId, terminal);

  useEffect(() => {
    setSearchDraft(q);
  }, [q]);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (searchDraft.trim() !== q) {
        updateParams({ q: searchDraft.trim() || null, page: null, case: null, attempt: null });
      }
    }, 350);
    return () => clearTimeout(timer);
    // updateParams is stable-by-intent; including it would reset the timer on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft, q]);

  useEffect(() => {
    if (!cases.data || cases.data.items.length > 0 || page <= 1) return;
    const total = cases.data.total;
    if (total > 0) {
      updateParams({ page: String(Math.max(1, Math.ceil(total / pageSize))) });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cases.data, page, pageSize]);

  const cancel = useMutation({
    mutationFn: () => cancelEvaluation(runId),
    onSuccess: () => {
      setCancelOpen(false);
      message.success("已请求取消，评测将在运行中的用例收口后进入已取消");
      queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
    },
    onError: (error) =>
      message.error(error instanceof Error ? error.message : "取消失败"),
  });
  const resumeBudget = useMutation({
    mutationFn: () =>
      resumeEvaluationBudget(runId, {
        additional_budget_cny: additionalBudget,
        budget_version: detail.data?.budget_version ?? 1,
      }),
    onSuccess: (result) => {
      setResumeOpen(false);
      message.success(`已增加预算，重新调度 ${result.attempts_created} 个未完成用例`);
      queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
      queryClient.invalidateQueries({ queryKey: ["eval-case"] });
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : "恢复失败");
      queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
    },
  });
  const retryCase = useMutation({
    mutationFn: (caseRunId: string) => retryEvaluationCase(caseRunId),
    onSuccess: (result) => {
      message.success(`已创建 attempt ${result.attempt.attempt_no}，旧 attempt 保留可追溯`);
      queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
      queryClient.invalidateQueries({ queryKey: ["eval-case"] });
      updateParams({ attempt: String(result.attempt.attempt_no) });
    },
    onError: (error) =>
      message.error(error instanceof Error ? error.message : "重试失败"),
  });
  const retryFailed = useMutation({
    mutationFn: () => retryFailedEvaluationCases(runId),
    onSuccess: (result) => {
      setBulkRetryOpen(false);
      message.success(
        result.retried
          ? `已重试 ${result.retried} 个用例${result.skipped ? `，跳过 ${result.skipped} 个` : ""}`
          : "没有可重试的失败用例",
      );
      queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
      queryClient.invalidateQueries({ queryKey: ["eval-case"] });
    },
    onError: (error) =>
      message.error(error instanceof Error ? error.message : "批量重试失败"),
  });

  const caseCounts: EvalCaseCounts = detail.data?.case_counts
    ? { ...EMPTY_CASE_COUNTS, ...(detail.data.case_counts as Partial<EvalCaseCounts>) }
    : {
        all: detail.data?.total_cases ?? 0,
        resolved: 0,
        unresolved: 0,
        running: detail.data?.running_count ?? 0,
        failed: detail.data?.failed_count ?? 0,
        queued: detail.data?.queued_count ?? 0,
      };
  const totalCases = detail.data?.total_cases ?? caseCounts.all;
  const finishedCount = Math.max(0, totalCases - caseCounts.queued - caseCounts.running);
  const progressPercent = totalCases ? Math.round((finishedCount / totalCases) * 100) : 0;
  const rateDenominator =
    caseCounts.resolved + caseCounts.unresolved + caseCounts.failed;
  const resolveRate = rateDenominator
    ? (caseCounts.resolved / rateDenominator) * 100
    : 0;
  const retryableCount = detail.data?.retryable_count ?? 0;
  const traceCounts = detail.data?.trace_counts ?? {};
  const budgetCny = detail.data?.model_budget_cny;
  const pausedBudget = detail.data?.status === "paused_budget";
  const pausingBudget = detail.data?.status === "pausing_budget";
  const unfinishedCount =
    detail.data?.unfinished_count ??
    (detail.data?.queued_count ?? 0) + (detail.data?.running_count ?? 0);
  const nextBudgetCny =
    budgetCny !== null && budgetCny !== undefined
      ? (Number(budgetCny) + additionalBudget).toFixed(2)
      : "—";
  const caseKeyById = useMemo(
    () =>
      new Map<string, string>(
        (detail.data?.cases ?? []).map((item) => [item.id, item.case_key]),
      ),
    [detail.data],
  );
  const availableCounts = cases.data?.counts ?? caseCounts;

  const changeTab = (key: string) => {
    const next = (TAB_KEYS as readonly string[]).includes(key) ? key : "overview";
    updateParams({ tab: next, case: null, attempt: null });
  };
  const openCase = (caseRunId: string, attempt?: number | null) => {
    updateParams({
      tab: "cases",
      case: caseRunId,
      attempt: attempt ? String(attempt) : null,
    });
  };
  const closeCase = () => updateParams({ case: null, attempt: null });

  if (detail.error) {
    return (
      <section className="page">
        <QueryError error={detail.error} onRetry={() => detail.refetch()} />
      </section>
    );
  }
  if (detail.isLoading || !detail.data) {
    return (
      <section className="page eval-detail-page">
        <Skeleton active paragraph={{ rows: 2 }} />
        <div className="grid grid-4 section-gap">
          {[0, 1, 2, 3].map((key) => (
            <div className="card metric-card" key={key}>
              <Skeleton active title={false} paragraph={{ rows: 2 }} />
            </div>
          ))}
        </div>
        <div className="card section-gap">
          <Skeleton active paragraph={{ rows: 6 }} />
        </div>
      </section>
    );
  }

  const data = detail.data;
  const resultState =
    terminal
      ? caseCounts.resolved === totalCases && totalCases > 0
        ? { label: "全部解决", color: "success" }
        : caseCounts.resolved > 0
          ? { label: "部分解决", color: "warning" }
          : { label: "未解决", color: "error" }
      : { label: "结果聚合中", color: "processing" };
  const traceFailed = traceCounts.failed ?? 0;
  const traceReady = traceCounts.ready ?? 0;
  const tracePending = traceCounts.pending ?? 0;
  const traceUnlinked = traceCounts.unlinked ?? 0;
  const headerDatasetLabel = config.data?.dataset
    ? `${config.data.dataset.dataset_name ?? config.data.dataset.dataset_slug} · ${config.data.dataset.version}`
    : data.dataset_version_id
      ? `数据集版本 ${data.dataset_version_id}`
      : "合成评测";

  return (
    <section className="page eval-detail-page">
      <PageHeading
        eyebrow={`Evaluation · ${runId}`}
        title={
          <span className="eval-title-row">
            {data.name || "评测详情"}
            {statusTag(data.status, runStatusMeta)}
          </span>
        }
        description={`${headerDatasetLabel} · ${data.runtime_key ?? "synthetic"} · 创建人 ${data.created_by}`}
        actions={
          <Space wrap>
            <Button icon={<Icon name="refresh" size={14} />} onClick={() => {
              void detail.refetch();
              queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
              queryClient.invalidateQueries({ queryKey: ["eval-case"] });
            }}>
              刷新
            </Button>
            <Button onClick={() => navigate("/evals")}>返回列表</Button>
            {pausedBudget ? (
              <Button type="primary" onClick={() => setResumeOpen(true)}>
                增加预算并恢复
              </Button>
            ) : null}
            {!terminal && data.status === "running" && retryableCount > 0 ? (
              <Button icon={<Icon name="refresh" size={14} />} onClick={() => setBulkRetryOpen(true)}>
                重试失败用例
              </Button>
            ) : null}
            {!terminal && data.status !== "cancelling" ? (
              <Button danger loading={cancel.isPending} onClick={() => setCancelOpen(true)}>
                取消评测
              </Button>
            ) : null}
            {data.status === "cancelling" ? <Tag color="warning">取消中</Tag> : null}
          </Space>
        }
      />

      <div className="eval-status-strip">
        <span className="eval-status-item">
          <span className="eval-status-label">执行</span>
          {statusTag(data.status, runStatusMeta)}
        </span>
        <span className="eval-status-item">
          <span className="eval-status-label">结果</span>
          <Tag color={resultState.color}>{resultState.label}</Tag>
        </span>
        <span className="eval-status-item">
          <span className="eval-status-label">轨迹处理</span>
          <Tag color={traceFailed ? "error" : tracePending ? "processing" : "success"}>
            ready {traceReady} · pending {tracePending}
            {traceFailed ? ` · failed ${traceFailed}` : ""}
            {traceUnlinked ? ` · unlinked ${traceUnlinked}` : ""}
          </Tag>
        </span>
        <span className="eval-status-item">
          <span className="eval-status-label">已用成本</span>
          <span className="mono">{formatMoney(data.budget_spent_cny)}</span>
        </span>
      </div>

      <Card className="eval-tabs-card">
        <Tabs
          activeKey={tab}
          onChange={changeTab}
          items={[
            { key: "overview", label: "实时进展" },
            {
              key: "cases",
              label: (
                <span>
                  用例明细 <span className="tab-count">{availableCounts.all}</span>
                </span>
              ),
            },
            { key: "results", label: "结果分析" },
            { key: "config", label: "评测配置" },
            { key: "events", label: "事件日志" },
          ]}
        />
      </Card>

      {tab === "overview" ? (
        <OverviewPanel
          detail={data}
          config={config.data}
          caseKeys={caseKeyById}
          counts={caseCounts}
          totalCases={totalCases}
          finishedCount={finishedCount}
          progressPercent={progressPercent}
          resolveRate={resolveRate}
          retryableCount={retryableCount}
          events={eventState.events}
          eventState={eventState}
          terminal={terminal}
          onResumeBudget={() => setResumeOpen(true)}
          onViewCases={(filter) =>
            updateParams({ tab: "cases", status: filter, case: null, attempt: null, page: null })
          }
          onOpenCase={openCase}
          onViewEvents={() => changeTab("events")}
        />
      ) : null}

      {tab === "cases" ? (
        <Card className="eval-panel-card eval-panel-flush">
          <div className="card-header eval-cases-head">
            <div>
              <h2 className="card-title">用例明细</h2>
              <p className="card-subtitle">
                共 {availableCounts.all} 个用例 · 状态计数不受当前页影响 · 点击行查看 attempt 与测试打点
              </p>
            </div>
            <div className="card-actions">
              <LiveBadge state={eventState} terminal={terminal} />
            </div>
          </div>
          <div className="eval-filter-bar">
            <Segmented
              value={status}
              onChange={(value) =>
                updateParams({ status: String(value) === "all" ? null : String(value), page: null, case: null, attempt: null })
              }
              options={CASE_STATUS_OPTIONS.map((option) => ({
                value: option.value,
                label: (
                  <span>
                    {option.label}
                    <span className="segment-count">{availableCounts[option.value] ?? 0}</span>
                  </span>
                ),
              }))}
            />
            <div className="eval-filter-controls">
              <Input
                allowClear
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                prefix={<Icon name="search" size={14} />}
                placeholder="搜索实例 ID 或仓库"
                style={{ width: 210 }}
              />
              <Select
                allowClear
                value={executionFilter || undefined}
                placeholder="执行状态"
                options={EXECUTION_OPTIONS}
                onChange={(value) => updateParams({ execution_status: value ?? null, page: null })}
                style={{ width: 116 }}
              />
              <Select
                allowClear
                value={resultFilter || undefined}
                placeholder="结果状态"
                options={RESULT_OPTIONS}
                onChange={(value) => updateParams({ result_status: value ?? null, page: null })}
                style={{ width: 116 }}
              />
              <Select
                allowClear
                value={traceFilter || undefined}
                placeholder="轨迹状态"
                options={TRACE_OPTIONS}
                onChange={(value) => updateParams({ trace_status: value ?? null, page: null })}
                style={{ width: 116 }}
              />
            </div>
          </div>
          {cases.error ? (
            <QueryError error={cases.error} onRetry={() => cases.refetch()} />
          ) : (
            <Table<EvalCaseRow>
              className="eval-case-table"
              rowKey="id"
              size="small"
              loading={cases.isLoading}
              dataSource={cases.data?.items ?? []}
              columns={caseColumns}
              onRow={(record) => ({
                onClick: () => openCase(record.id, record.latest_attempt_no),
                onKeyDown: (event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openCase(record.id, record.latest_attempt_no);
                  }
                },
                tabIndex: 0,
                role: "button",
                style: { cursor: "pointer" },
              })}
              pagination={{
                current: page,
                pageSize,
                total: cases.data?.total ?? 0,
                showSizeChanger: true,
                pageSizeOptions: [20, 50, 100],
                showTotal: (total) => `共 ${total} 个用例`,
                onChange: (nextPage, nextSize) =>
                  updateParams({
                    page: String(nextPage),
                    pageSize: String(nextSize),
                    case: null,
                    attempt: null,
                  }),
              }}
              locale={{
                emptyText: (
                  <EmptyState
                    icon="search"
                    title="当前筛选下没有用例"
                    description="调整状态、执行/结果/轨迹筛选或搜索条件后再试。"
                    compact
                  />
                ),
              }}
            />
          )}
        </Card>
      ) : null}

      {tab === "results" ? (
        <ResultsPanel
          results={results.data}
          loading={results.isLoading}
          error={results.error}
          onRetry={() => results.refetch()}
          counts={caseCounts}
          totalCases={totalCases}
          finishedCount={finishedCount}
          progressPercent={progressPercent}
          onOpenCases={() => updateParams({ tab: "cases", status: null, page: null })}
        />
      ) : null}

      {tab === "config" ? (
        <ConfigPanel
          config={config.data}
          loading={config.isLoading}
          error={config.error}
          onRetry={() => config.refetch()}
        />
      ) : null}

      {tab === "events" ? (
        <EventsPanel
          runId={runId}
          state={eventState}
          caseKeys={caseKeyById}
          terminal={terminal}
          onOpenCase={openCase}
        />
      ) : null}

      <CaseDrawer
        open={Boolean(selectedCaseId)}
        caseId={selectedCaseId}
        caseQuery={caseDetail}
        testsQuery={tests}
        selectedAttempt={selectedAttempt}
        onAttemptChange={(attempt) => updateParams({ attempt: String(attempt) })}
        onClose={closeCase}
        onRetry={() => {
          if (selectedCaseId) retryCase.mutate(selectedCaseId);
        }}
        retrying={retryCase.isPending}
      />

      <Modal
        open={cancelOpen}
        title="取消评测"
        okText="确认取消评测"
        cancelText="保留评测"
        okButtonProps={{ danger: true }}
        confirmLoading={cancel.isPending}
        onOk={() => cancel.mutate()}
        onCancel={() => setCancelOpen(false)}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="取消是异步操作，不会立即伪装成已取消"
          description={`将停止派发新分片，未开始的 attempt 立即取消；已运行的 ${caseCounts.running} 个用例会先上传已有产物再收口。已完成用例的结果与费用保留，未完成用例不会生成最终指标。`}
        />
        <SummaryList
          items={[
            ["评测用例", `${totalCases} 个`],
            ["已完成", `${finishedCount} 个`],
            ["运行中", `${caseCounts.running} 个`],
            ["排队", `${caseCounts.queued} 个`],
            ["数据保留", "轨迹、测试打点与旧 attempt 均保留"],
          ]}
        />
      </Modal>

      <Modal
        open={bulkRetryOpen}
        title="重试失败用例"
        okText={`重试 ${retryableCount} 个用例`}
        cancelText="取消"
        confirmLoading={retryFailed.isPending}
        okButtonProps={{ disabled: retryableCount === 0 }}
        onOk={() => retryFailed.mutate()}
        onCancel={() => setBulkRetryOpen(false)}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="只重试失败 / 超时且仍有 attempt 余额的用例"
          description={`将为 ${retryableCount} 个用例创建新 attempt 并重新派发；已完成用例不会重跑，旧 attempt 的轨迹与评分保持不变。`}
        />
        <SummaryList
          items={[
            ["可重试", `${retryableCount} 个`],
            ["失败 / 超时", `${caseCounts.failed} 个`],
            ["最大 attempts", `每个用例 ${data.max_attempts ?? 2} 次`],
          ]}
        />
      </Modal>

      <Modal
        open={resumeOpen}
        title="增加模型预算并恢复"
        okText="确认增加并恢复"
        cancelText="取消"
        confirmLoading={resumeBudget.isPending}
        okButtonProps={{ disabled: !(additionalBudget > 0) }}
        onOk={() => resumeBudget.mutate()}
        onCancel={() => setResumeOpen(false)}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 8, marginBottom: 16 }}
          message="只重新调度未完成用例"
          description="已完成用例不会重跑，被预算打断的用例会创建新 attempt；恢复期间不会切换模型或资源池。"
        />
        <SummaryList
          items={[
            ["当前预算", formatMoney(budgetCny)],
            ["已花费", formatMoney(data.budget_spent_cny)],
            ["未完成用例", `${unfinishedCount} 个`],
            ["预算版本", `v${data.budget_version ?? 1}`],
            ["暂停原因", pausingBudget ? "预算用尽，正在收口运行中的用例" : pausedBudget ? "模型预算用尽" : "—"],
          ]}
        />
        <div style={{ marginTop: 16, marginBottom: 6 }}>新增预算（CNY）</div>
        <InputNumber
          min={0.01}
          step={1}
          precision={2}
          value={additionalBudget}
          onChange={(value) => setAdditionalBudget(typeof value === "number" ? value : 0)}
          style={{ width: "100%" }}
        />
        <div className="muted small" style={{ marginTop: 8 }}>
          恢复后总预算约 {nextBudgetCny} CNY；实际费用仍以模型网关记录为准。
        </div>
      </Modal>
    </section>
  );
}

const caseColumns: TableColumnsType<EvalCaseRow> = [
  {
    title: "用例",
    dataIndex: "case_key",
    key: "case_key",
    render: (_value, record) => (
      <div>
        <div className="table-primary mono">{record.case_key}</div>
        <div className="table-secondary">
          #{record.ordinal}
          {record.repo ? ` · ${record.repo}` : ""} · attempt {record.latest_attempt_no}
          {record.error_code ? ` · ${record.error_code}` : ""}
        </div>
      </div>
    ),
  },
  {
    title: "执行",
    dataIndex: "execution_status",
    key: "execution_status",
    width: 96,
    render: (value: string) => statusTag(value, executionStatusMeta),
  },
  {
    title: "结果",
    dataIndex: "result_status",
    key: "result_status",
    width: 96,
    render: (value: string) => statusTag(value, resultStatusMeta),
  },
  {
    title: "轨迹",
    dataIndex: "trace_status",
    key: "trace_status",
    width: 96,
    render: (value: string) => statusTag(value, traceStatusMeta),
  },
  {
    title: "测试打点",
    dataIndex: "test_summary",
    key: "test_summary",
    render: (_value, record) => {
      const summary = record.test_summary;
      const total = summary.passed + summary.failed + summary.skipped + summary.error;
      if (!total) return <span className="muted small">—</span>;
      return (
        <span className="test-inline">
          <span className="test-pass">P {summary.passed}</span>
          {summary.failed ? <span className="test-fail">F {summary.failed}</span> : null}
          {summary.f2p_failed ? <span className="test-fail">F2P {summary.f2p_failed}</span> : null}
        </span>
      );
    },
  },
  {
    title: "耗时",
    dataIndex: "duration_ms",
    key: "duration_ms",
    width: 96,
    render: (value: number | null) => formatDuration(value),
  },
  {
    title: "",
    key: "open",
    width: 72,
    render: () => (
      <span className="eval-row-link">
        查看 <Icon name="arrowRight" size={12} />
      </span>
    ),
  },
];

interface OverviewPanelProps {
  detail: EvaluationDetail;
  config: EvalConfigSnapshot | undefined;
  caseKeys: Map<string, string>;
  counts: EvalCaseCounts;
  totalCases: number;
  finishedCount: number;
  progressPercent: number;
  resolveRate: number;
  retryableCount: number;
  events: EvaluationEvent[];
  eventState: EvalEventsState;
  terminal: boolean;
  onResumeBudget: () => void;
  onViewCases: (filter: string) => void;
  onOpenCase: (caseRunId: string, attempt?: number | null) => void;
  onViewEvents: () => void;
}

function OverviewPanel(props: OverviewPanelProps) {
  const {
    detail,
    config,
    caseKeys,
    counts,
    totalCases,
    finishedCount,
    progressPercent,
    resolveRate,
    retryableCount,
    events,
    eventState,
    terminal,
    onResumeBudget,
    onViewCases,
    onOpenCase,
    onViewEvents,
  } = props;
  const hasBudget = detail.model_budget_cny !== null && detail.model_budget_cny !== undefined;
  const pausedBudget = detail.status === "paused_budget";
  const pausingBudget = detail.status === "pausing_budget";
  const unfinishedCount =
    detail.unfinished_count ?? (detail.queued_count ?? 0) + (detail.running_count ?? 0);
  const failedCases = (detail.cases ?? []).filter((item) =>
    ["failed", "timeout"].includes(item.execution_status),
  );
  const runningCases = (detail.cases ?? []).filter(
    (item) => item.execution_status === "running",
  );
  const keyCases = [...runningCases, ...failedCases].slice(0, 6);
  const estimateMinutes =
    !terminal && detail.started_at && progressPercent >= 5 && progressPercent < 100
      ? Math.max(
          1,
          Math.round(
            ((Date.now() - new Date(detail.started_at).getTime()) *
              (100 - progressPercent)) /
              progressPercent /
              60000,
          ),
        )
      : null;
  const recentEvents = events.slice(-6).reverse();
  const datasetLabel = config?.dataset
    ? `${config.dataset.dataset_name ?? config.dataset.dataset_slug} · ${config.dataset.version}`
    : detail.dataset_version_id ?? "合成评测";

  return (
    <>
      <div className="grid grid-4 section-gap">
        <StatusCard
          label="已完成用例"
          value={`${finishedCount} / ${totalCases}`}
          meta={terminal ? "全部状态已结算" : "实时心跳更新"}
          icon="check"
          tone="success"
        />
        <StatusCard
          label="已解决"
          value={String(counts.resolved)}
          meta={`完成用例中的解决率 ${resolveRate.toFixed(1)}%`}
          icon="sparkles"
          tone="violet"
        />
        <StatusCard
          label="执行中"
          value={String(counts.running)}
          meta={`并发 ${counts.running} / ${detail.effective_concurrency ?? detail.requested_concurrency ?? 1}`}
          icon="cpu"
        />
        <StatusCard
          label="失败 / 超时"
          value={String(counts.failed)}
          meta={detail.status === "running" ? `可重试 ${retryableCount} 个` : "结果已归类"}
          icon="alert"
          tone="warning"
        />
      </div>

      <Card className="section-gap eval-progress-card">
        <div className="eval-hero">
          <div>
            <div className="row-between">
              <div>
                <div className="eyebrow">{terminal ? "Final progress" : "Live progress"}</div>
                <h2 className="card-title">
                  {finishedCount} / {totalCases} 个用例已结束
                </h2>
              </div>
              <LiveBadge state={eventState} terminal={terminal} />
            </div>
            <div className="eval-progress-number">
              <strong>{progressPercent}%</strong>
              <span>
                {terminal
                  ? "结果已固化"
                  : estimateMinutes
                    ? `预计剩余 ${estimateMinutes} 分钟`
                    : "按同一状态汇总实时计算"}
              </span>
            </div>
            <StackedProgress counts={counts} total={totalCases} />
            {counts.failed > 0 ? (
              <Alert
                className="eval-progress-banner"
                type="warning"
                showIcon
                message={`${counts.failed} 个用例失败 / 超时`}
                description="调度器已按失败类型归类；可在用例明细中筛选失败用例，查看 attempt、测试打点与失败日志。"
                action={
                  <Button size="small" onClick={() => onViewCases("failed")}>
                    查看失败用例
                  </Button>
                }
              />
            ) : terminal ? (
              <Alert
                className="eval-progress-banner"
                type="success"
                showIcon
                message="全部用例执行完成"
                description="没有失败或超时用例；结果与事件日志已固化。"
              />
            ) : null}
          </div>
          <div className="eval-context">
            <div className="eval-context-item">
              <div className="eval-context-label">数据集</div>
              <div className="eval-context-value">{datasetLabel}</div>
            </div>
            <div className="eval-context-item">
              <div className="eval-context-label">Runtime</div>
              <div className="eval-context-value">
                {detail.runtime_key ?? "synthetic"} {detail.runtime_version ?? ""}
              </div>
            </div>
            <div className="eval-context-item">
              <div className="eval-context-label">模型</div>
              <div className="eval-context-value">{detail.model_name ?? "未绑定模型"}</div>
            </div>
            <div className="eval-context-item">
              <div className="eval-context-label">资源池</div>
              <div className="eval-context-value">{config?.resource_pool?.name ?? detail.resource_pool_id ?? "—"}</div>
            </div>
            <div className="eval-context-item">
              <div className="eval-context-label">并发</div>
              <div className="eval-context-value">
                {counts.running} / {detail.effective_concurrency ?? detail.requested_concurrency ?? 1} Runner
              </div>
            </div>
            <div className="eval-context-item">
              <div className="eval-context-label">已用成本</div>
              <div className="eval-context-value">{formatMoney(detail.budget_spent_cny)}</div>
            </div>
            <div className="eval-context-item">
              <div className="eval-context-label">启动时间</div>
              <div className="eval-context-value">{formatTime(detail.started_at)}</div>
            </div>
          </div>
        </div>
      </Card>

      {hasBudget ? (
        <Card title="模型预算" className="section-gap eval-panel-card">
          {pausingBudget ? (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="预算暂停中"
              description={`调度器已停止派发新分片，正在等待运行中的 ${detail.running_count ?? 0} 个用例上传已有产物并安全退出；收口后即可增加预算恢复。`}
            />
          ) : null}
          {pausedBudget ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="预算已暂停，评测尚未结束"
              description={`已花费 ${formatMoney(detail.budget_spent_cny)} / 预算 ${formatMoney(detail.model_budget_cny)}，${unfinishedCount} 个用例未完成。增加预算后只会重新调度未完成用例；已完成用例的结果与成本保持不变。`}
              action={
                <Button type="primary" size="small" onClick={onResumeBudget}>
                  增加预算并恢复
                </Button>
              }
            />
          ) : null}
          <SummaryList
            items={[
              ["模型预算", formatMoney(detail.model_budget_cny)],
              ["已花费", formatMoney(detail.budget_spent_cny)],
              ["剩余", formatMoney(detail.budget_remaining_cny)],
              ["预算版本", `v${detail.budget_version ?? 1}`],
              ["已完成", `${finishedCount} 个`],
              ["未完成", `${unfinishedCount} 个`],
            ]}
          />
        </Card>
      ) : null}

      <div className="grid dashboard-grid section-gap">
        <Card className="eval-panel-card eval-panel-flush">
          <div className="card-header">
            <div>
              <h2 className="card-title">关键用例</h2>
              <p className="card-subtitle">
                仅显示正在执行与最近失败，完整用例在独立页签分页查看
              </p>
            </div>
            <div className="card-actions">
              <Button size="small" type="link" onClick={() => onViewCases("all")}>
                查看全部 <Icon name="arrowRight" size={12} />
              </Button>
            </div>
          </div>
          <div className="card-body">
            {keyCases.length ? (
              <div className="eval-key-case-list">
                {keyCases.map((item) => (
                  <button
                    type="button"
                    className="eval-key-case-row"
                    key={item.id}
                    onClick={() => onOpenCase(item.id, item.latest_attempt_no)}
                  >
                    <span className="eval-key-case-id">
                      <span className="mono">{item.case_key}</span>
                      <span className="muted small">
                        {item.repo ?? `用例 #${item.ordinal}`}
                        {item.error_code ? ` · ${item.error_code}` : ""}
                      </span>
                    </span>
                    {statusTag(item.execution_status, executionStatusMeta)}
                    <span className="mono small">{formatDuration(item.duration_ms)}</span>
                    <Icon name="arrowRight" size={12} />
                  </button>
                ))}
              </div>
            ) : (
              <EmptyState
                icon="check"
                title={terminal ? "没有执行中或失败用例" : "当前没有执行中或失败用例"}
                description="运行中的用例与最近失败会在这里抽样展示。"
                compact
              />
            )}
          </div>
        </Card>
        <Card className="eval-panel-card eval-panel-flush">
          <div className="card-header">
            <div>
              <h2 className="card-title">实时事件</h2>
              <p className="card-subtitle">来自 Runner 回调与调度器</p>
            </div>
            <div className="card-actions">
              <Button size="small" type="link" onClick={onViewEvents}>
                查看全部
              </Button>
            </div>
          </div>
          <div className="card-body">
            {recentEvents.length ? (
              <div className="eval-event-list">
                {recentEvents.map((event) => (
                  <div className="eval-event-row" key={event.id}>
                    <span
                      className={`eval-event-dot ${
                        event.event_type.includes("failed") || event.event_type.includes("timeout")
                          ? "danger"
                          : event.event_type.includes("completed") || event.event_type.includes("resumed")
                            ? "success"
                            : ""
                      }`}
                    />
                    <div className="eval-event-copy">
                      <div>{eventSummary(event, caseKeys)}</div>
                      <div className="muted small">
                        {eventTypeLabels[event.event_type] ?? event.event_type} · {formatTime(event.occurred_at)}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState icon="pulse" title="尚未收到事件" description="评测开始后事件会实时出现在这里。" compact />
            )}
          </div>
        </Card>
      </div>
    </>
  );
}

interface ResultsPanelProps {
  results: { is_final: boolean; items: MetricResult[] } | undefined;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
  counts: EvalCaseCounts;
  totalCases: number;
  finishedCount: number;
  progressPercent: number;
  onOpenCases: () => void;
}

function ResultsPanel(props: ResultsPanelProps) {
  const { results, loading, error, onRetry, counts, totalCases, finishedCount, progressPercent, onOpenCases } = props;
  if (error) {
    return <Card><QueryError error={error} onRetry={onRetry} /></Card>;
  }
  if (loading && !results) {
    return <Card><Skeleton active paragraph={{ rows: 8 }} /></Card>;
  }
  const metrics = results?.items ?? [];
  const known = metrics.filter((metric) => isKnownMetricKind(metric.display_kind));
  const unknown = metrics.filter((metric) => !isKnownMetricKind(metric.display_kind));
  return (
    <>
      {results && !results.is_final ? (
        <Alert
          className="section-gap"
          type="warning"
          showIcon
          message="结果尚在聚合中"
          description={`当前展示已完成 ${finishedCount} 个用例的快照；最终指标将在全部用例结束后固化，不把部分结果当作最终结论。`}
        />
      ) : null}
      <div className="grid grid-4 section-gap">
        {known.length ? (
          known.map((metric) => (
            <div className="card eval-metric-card" key={metric.metric_key}>
              <div className="eval-metric-head">
                <span className="eval-metric-name">{metric.display_name}</span>
                <Tag color={results?.is_final ? "success" : "processing"}>
                  {results?.is_final ? "最终" : "快照"}
                </Tag>
              </div>
              <div className="eval-metric-value" title={formatMetricValue(metric)}>
                {formatMetricValue(metric)}
              </div>
              <div className="eval-metric-meta">
                {formatDecimal(metric.numerator)} / {formatDecimal(metric.denominator)} · 分母：
                {denominatorPolicyLabel(metric.denominator_policy)}
              </div>
              {metric.formula_text ? (
                <Tooltip title={metric.formula_text}>
                  <div className="eval-metric-formula">{metric.formula_text}</div>
                </Tooltip>
              ) : null}
              <div className="eval-metric-foot">
                口径 v{metric.metric_version} · {formatTime(metric.generated_at)}
              </div>
            </div>
          ))
        ) : (
          <Card className="eval-metric-card">
            <EmptyState
              icon="sparkles"
              title="尚无结果快照"
              description="用例完成后会自动生成指标；最终指标只在全部用例结束后写入。"
              compact
            />
          </Card>
        )}
      </div>
      <Card title="用例结果分布" className="section-gap eval-panel-card eval-panel-flush">
        <div className="card-body">
          <div className="row-between">
            <div>
              <h2 className="card-title">{counts.resolved} / {totalCases} 个用例已解决</h2>
              <p className="card-subtitle">按数据集注册的评分口径统计，分母与口径就近展示</p>
            </div>
            <Tag color={results?.is_final ? "success" : "processing"}>
              {progressPercent}% 已结束
            </Tag>
          </div>
          <div style={{ marginTop: 16 }}>
            <StackedProgress counts={counts} total={totalCases} />
          </div>
          <div className="eval-result-actions">
            <Button onClick={onOpenCases}>打开用例明细</Button>
          </div>
        </div>
      </Card>
      {unknown.length ? (
        <Card title="未知指标类型（安全降级）" className="section-gap eval-panel-card">
          <p className="muted small">
            这些指标声明了前端未注册的 display_kind；页面只展示原始值，不执行动态组件代码。
          </p>
          <div className="eval-unknown-metrics">
            {unknown.map((metric) => (
              <div className="eval-unknown-metric" key={metric.metric_key}>
                <div className="eval-metric-head">
                  <span className="eval-metric-name">{metric.display_name}</span>
                  <Tag>{metric.display_kind}</Tag>
                </div>
                <pre className="eval-pre">{JSON.stringify(metric.value, null, 2)}</pre>
                <div className="muted small">{metric.metric_key} · v{metric.metric_version}</div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}
      {unknown.length === 0 && metrics.length === 0 ? (
        <Card className="section-gap eval-panel-card">
          <EmptyState
            icon="list"
            title="还没有结果指标"
            description="等待用例完成并写入指标快照后再查看。"
          />
        </Card>
      ) : null}
    </>
  );
}

interface ConfigPanelProps {
  config: EvalConfigSnapshot | undefined;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
}

function ConfigPanel({ config, loading, error, onRetry }: ConfigPanelProps) {
  if (error) {
    return <Card className="section-gap"><QueryError error={error} onRetry={onRetry} /></Card>;
  }
  if (loading || !config) {
    return <Card className="section-gap"><Skeleton active paragraph={{ rows: 10 }} /></Card>;
  }
  const assetRows = config.assets.items.map((item, index) => ({
    key: `${String(item.asset_type ?? "asset")}-${String(item.path ?? index)}`,
    asset_type: String(item.asset_type ?? "asset"),
    name: String(item.name ?? ""),
    path: String(item.path ?? ""),
    content_digest: typeof item.content_digest === "string" ? item.content_digest : null,
  }));
  return (
    <>
      <div className="grid grid-2 section-gap">
        <Card title="执行组合" className="eval-panel-card">
          <p className="card-subtitle" style={{ padding: "0 0 12px" }}>
            创建评测时冻结的输入快照
          </p>
          <SummaryList
            items={[
              ["数据集", config.dataset ? `${config.dataset.dataset_name ?? config.dataset.dataset_slug} · ${config.dataset.version}` : "合成评测"],
              ["数据集来源", config.dataset ? `${config.dataset.source_uri || "—"} @ ${config.dataset.source_revision}` : "—"],
              ["适配器版本", config.dataset?.adapter_version ?? "—"],
              ["用例数", config.dataset ? `${config.dataset.case_count} 个` : "—"],
              ["Runtime", `${config.runtime.key ?? "—"} ${config.runtime.version ?? ""}`],
              ["模型", config.model ? `${config.model.model_name} · ${config.model.provider}` : config.model ? "—" : "未绑定模型"],
              ["模型凭据", config.model ? (config.model.is_temporary ? "临时模型，评测结束按策略销毁" : "平台模型网关托管") : "—"],
              ["资源池", config.resource_pool ? `${config.resource_pool.name} · ${config.resource_pool.provisioning_mode}` : "—"],
              ["Agent 资产", `${config.assets.asset_count ?? 0} 项 / ${config.assets.file_count ?? 0} 个文件`],
              ["资产 Ref", config.assets.requested_ref ?? "—"],
              ["解析 commit", config.assets.resolved_commit ?? "—"],
            ]}
          />
        </Card>
        <Card title="执行策略" className="eval-panel-card">
          <p className="card-subtitle" style={{ padding: "0 0 12px" }}>
            超时、重试、容量与镜像
          </p>
          <SummaryList
            items={[
              ["单用例超时", formatSeconds(config.execution.case_timeout_seconds)],
              ["失败重试上限", `${config.execution.max_attempts} 次`],
              ["分片大小", `${config.execution.shard_size} 用例 / 次 dispatch`],
              ["请求并发", String(config.execution.requested_concurrency)],
              ["有效并发", config.execution.effective_concurrency !== null ? String(config.execution.effective_concurrency) : "—"],
              ["排队原因", config.execution.queue_reason ?? "—"],
              ["执行镜像", shortDigest(config.execution.execution_image_digest)],
              ["评分镜像", shortDigest(config.execution.scoring_image_digest)],
              ["任务清单", shortDigest(config.execution.task_manifest_digest)],
            ]}
          />
        </Card>
        <Card title="模型预算" className="eval-panel-card">
          <SummaryList
            items={[
              ["预算", formatMoney(config.budget.model_budget_cny)],
              ["已花费", formatMoney(config.budget.budget_spent_cny)],
              ["剩余", formatMoney(config.budget.budget_remaining_cny)],
              ["预算版本", `v${config.budget.budget_version}`],
              ["暂停原因", config.budget.pause_reason ?? "—"],
            ]}
          />
        </Card>
        <Card title="一致性预检" className="eval-panel-card">
          <SummaryList
            items={[
              ["有效并发", config.consistency.effective_concurrency !== null ? String(config.consistency.effective_concurrency) : "—"],
              ["阻塞项", String(config.consistency.blockers.length)],
              ["警告", String(config.consistency.warnings.length)],
            ]}
          />
          {config.consistency.warnings.length ? (
            <div className="eval-issue-list">
              {config.consistency.warnings.map((warning, index) => (
                <Alert
                  key={`${String(warning.code ?? index)}`}
                  type="warning"
                  showIcon
                  message={String(warning.message ?? warning.code ?? "警告")}
                  description={warning.details ? <pre className="eval-pre">{JSON.stringify(warning.details, null, 2)}</pre> : undefined}
                />
              ))}
            </div>
          ) : null}
          {config.consistency.blockers.length ? (
            <div className="eval-issue-list">
              {config.consistency.blockers.map((blocker, index) => (
                <Alert
                  key={`${String(blocker.code ?? index)}`}
                  type="error"
                  showIcon
                  message={String(blocker.message ?? blocker.code ?? "阻塞项")}
                />
              ))}
            </div>
          ) : null}
        </Card>
      </div>

      <Card title="Agent 资产快照" className="section-gap eval-panel-card">
        <p className="muted small">
          {config.assets.snapshot_id ?? "无资产快照"} · 树摘要 {shortDigest(config.assets.tree_digest)} · 包摘要{" "}
          {shortDigest(config.assets.bundle_digest)} · {config.assets.bundle_size ?? 0} B
        </p>
        <Table
          rowKey="key"
          size="small"
          dataSource={assetRows}
          pagination={false}
          columns={[
            { title: "类型", dataIndex: "asset_type", width: 100, render: (value: string) => <Tag>{value}</Tag> },
            { title: "资产", dataIndex: "name" },
            { title: "路径", dataIndex: "path", render: (value: string) => <span className="mono small">{value}</span> },
            {
              title: "摘要",
              dataIndex: "content_digest",
              width: 150,
              render: (value: string | null) => <span className="mono small">{shortDigest(value)}</span>,
            },
          ]}
          locale={{ emptyText: <EmptyState icon="boxes" title="本次评测没有冻结 Agent 资产" compact /> }}
        />
      </Card>

      <Card title="指标口径" className="section-gap eval-panel-card">
        <Table
          rowKey="id"
          size="small"
          dataSource={config.metrics}
          pagination={false}
          columns={[
            { title: "指标", dataIndex: "display_name" },
            { title: "Key", dataIndex: "metric_key", render: (value: string) => <span className="mono small">{value}</span> },
            {
              title: "展示",
              dataIndex: "display_kind",
              width: 100,
              render: (value: string) => (
                <Tooltip title={value}>
                  <Tag>{displayKindLabels[value] ?? value}</Tag>
                </Tooltip>
              ),
            },
            {
              title: "方向",
              dataIndex: "direction",
              width: 120,
              render: (value: string) => directionLabel(value),
            },
            {
              title: "分母口径",
              dataIndex: "denominator_policy",
              render: (value: string | null) => denominatorPolicyLabel(value),
            },
            { title: "公式", dataIndex: "formula_text", render: (value: string | null) => <span className="small muted">{value ?? "—"}</span> },
          ]}
          locale={{ emptyText: <EmptyState icon="sparkles" title="数据集没有注册指标定义" compact /> }}
        />
      </Card>
    </>
  );
}

interface EventsPanelProps {
  runId: string;
  state: EvalEventsState;
  caseKeys: Map<string, string>;
  terminal: boolean;
  onOpenCase: (caseRunId: string, attempt?: number | null) => void;
}

function EventsPanel({ runId, state, caseKeys, terminal, onOpenCase }: EventsPanelProps) {
  const [older, setOlder] = useState<EvaluationEvent[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<Error | null>(null);

  useEffect(() => {
    setOlder([]);
    setCursor(null);
    setHasMore(false);
    setLoadingOlder(false);
    setOlderError(null);
  }, [runId]);

  useEffect(() => {
    if (older.length === 0 && cursor === null && state.olderCursor !== null) {
      setCursor(state.olderCursor);
      setHasMore(state.hasOlder);
    }
  }, [state.olderCursor, state.hasOlder, older.length, cursor]);

  const displayed = useMemo(
    () => mergeEvents(older, state.events).slice().reverse(),
    [older, state.events],
  );

  const loadOlder = async () => {
    const before = cursor ?? state.olderCursor;
    if (!before) return;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await fetchEvaluationEvents(runId, { beforeId: before, limit: 100 });
      setOlder((current) => mergeEvents(current, page.items));
      setCursor(page.has_more ? page.next_cursor : null);
      setHasMore(page.has_more);
    } catch (error) {
      setOlderError(error instanceof Error ? error : new Error("加载更早事件失败"));
    } finally {
      setLoadingOlder(false);
    }
  };

  const columns: TableColumnsType<EvaluationEvent> = [
    {
      title: "时间",
      dataIndex: "occurred_at",
      width: 180,
      render: (value: string | null) => <span className="small mono">{formatTime(value)}</span>,
    },
    {
      title: "类型",
      dataIndex: "event_type",
      width: 190,
      render: (value: string) => (
        <Space size={4}>
          <Tag color={value.includes("failed") || value.includes("timeout") ? "error" : value.includes("completed") || value.includes("resumed") ? "success" : "processing"}>
            {value}
          </Tag>
          <span className="small muted">{eventTypeLabels[value] ?? ""}</span>
        </Space>
      ),
    },
    {
      title: "事件",
      key: "summary",
      render: (_value, record) => <span>{eventSummary(record, caseKeys)}</span>,
    },
    {
      title: "关联对象",
      key: "object",
      width: 220,
      render: (_value, record) => {
        const label = record.case_run_id ? caseKeys.get(record.case_run_id) ?? record.case_run_id : "评测";
        return (
          <span className="mono small">
            {label}
            {record.attempt_no ? ` · attempt ${record.attempt_no}` : ""}
          </span>
        );
      },
    },
    {
      title: "",
      key: "open",
      width: 90,
      render: (_value, record) =>
        record.case_run_id ? (
          <Button
            size="small"
            type="link"
            onClick={() => onOpenCase(record.case_run_id as string, record.attempt_no)}
          >
            查看用例
          </Button>
        ) : null,
    },
  ];

  return (
    <Card className="section-gap eval-panel-card eval-panel-flush">
      <div className="card-header">
        <div>
          <h2 className="card-title">评测事件日志</h2>
          <p className="card-subtitle">用于排查调度、预算、取消、重试与轨迹处理问题；事件来自数据库游标回放</p>
        </div>
        <div className="card-actions">
          <LiveBadge state={state} terminal={terminal} />
          <Button
            size="small"
            loading={loadingOlder}
            disabled={!hasMore && cursor === null}
            onClick={() => void loadOlder()}
          >
            加载更早事件
          </Button>
        </div>
      </div>
      {olderError ? <QueryError error={olderError} onRetry={() => void loadOlder()} /> : null}
      <Table<EvaluationEvent>
        rowKey="id"
        size="small"
        loading={state.loading}
        dataSource={displayed}
        columns={columns}
        pagination={false}
        expandable={{
          expandedRowRender: (record) => (
            <pre className="eval-pre">{JSON.stringify(record.payload, null, 2)}</pre>
          ),
          rowExpandable: (record) => Object.keys(record.payload).length > 0,
        }}
        locale={{
          emptyText: (
            <EmptyState
              icon="pulse"
              title="还没有事件"
              description="评测创建、派发、预算、取消与用例回调都会写入事件日志。"
              compact
            />
          ),
        }}
      />
    </Card>
  );
}

interface CaseDrawerProps {
  open: boolean;
  caseId: string | null;
  caseQuery: {
    data: EvalCaseDetail | undefined;
    isLoading: boolean;
    error: Error | null;
    refetch: () => void;
  };
  testsQuery: {
    data: { attempt: number | null; items: CaseTestResult[]; summary: Record<string, number> } | undefined;
    isLoading: boolean;
    error: Error | null;
    refetch: () => void;
  };
  selectedAttempt: number | null;
  onAttemptChange: (attempt: number) => void;
  onClose: () => void;
  onRetry: () => void;
  retrying: boolean;
}

const testColumns: TableColumnsType<CaseTestResult> = [
  {
    title: "组",
    dataIndex: "test_group",
    width: 78,
    render: (value: string) => (
      <Tag color={value === "F2P" ? "blue" : value === "P2P" ? "purple" : "default"}>{value}</Tag>
    ),
  },
  {
    title: "测试",
    dataIndex: "test_name",
    render: (_value, record) => (
      <div>
        <div className="mono small">{record.test_name}</div>
        <div className="muted small">
          {record.suite_name}
          {record.file_path ? ` · ${record.file_path}${record.line_no ? `:${record.line_no}` : ""}` : ""}
        </div>
      </div>
    ),
  },
  {
    title: "结果",
    dataIndex: "outcome",
    width: 90,
    render: (value: string) => (
      <Tag color={value === "passed" ? "success" : value === "failed" ? "error" : value === "error" ? "warning" : "default"}>
        {value}
      </Tag>
    ),
  },
  {
    title: "原生状态",
    dataIndex: "native_status",
    width: 110,
    render: (value: string) => <span className="small">{value || "—"}</span>,
  },
  {
    title: "耗时",
    dataIndex: "duration_ms",
    width: 90,
    render: (value: number | null) => formatDuration(value),
  },
  {
    title: "失败日志",
    key: "log",
    width: 110,
    render: (_value, record) =>
      record.failure_message || record.log_object_key ? <span className="small">展开查看</span> : <span className="muted small">—</span>,
  },
];

function CaseDrawer({
  open,
  caseId,
  caseQuery,
  testsQuery,
  selectedAttempt,
  onAttemptChange,
  onClose,
  onRetry,
  retrying,
}: CaseDrawerProps) {
  const [retryConfirmOpen, setRetryConfirmOpen] = useState(false);
  const [logRecord, setLogRecord] = useState<CaseTestResult | null>(null);
  const detail = caseQuery.data;
  const selectedSummary =
    detail && selectedAttempt !== null ? attemptsSummary(detail.attempts, selectedAttempt) : null;
  const retryBlocked = detail?.retry.reason
    ? retryBlockedLabels[detail.retry.reason] ?? detail.retry.reason
    : null;

  useEffect(() => {
    if (!open) {
      setRetryConfirmOpen(false);
      setLogRecord(null);
    }
  }, [open]);

  return (
    <>
      <Drawer
        open={open}
        onClose={onClose}
        width={960}
        title={
          caseId && detail
            ? `${detail.case.case_key} · attempt ${selectedAttempt ?? detail.case.latest_attempt_no}`
            : "用例详情"
        }
      >
        {caseQuery.error ? (
          <QueryError error={caseQuery.error} onRetry={caseQuery.refetch} />
        ) : caseQuery.isLoading || !detail ? (
          <Skeleton active paragraph={{ rows: 8 }} />
        ) : (
          <div className="case-drawer">
            <div className="case-drawer-head">
              <Space wrap>
                {statusTag(detail.case.execution_status, executionStatusMeta)}
                {statusTag(detail.case.result_status, resultStatusMeta)}
                {statusTag(detail.case.trace_status, traceStatusMeta)}
                <span className="muted small">
                  最新 attempt {detail.case.latest_attempt_no} / 上限 {detail.run.max_attempts}
                </span>
                <span className="muted small">单用例超时 {formatSeconds(detail.run.case_timeout_seconds)}</span>
              </Space>
              {detail.retry.retryable ? (
                <Button type="primary" loading={retrying} onClick={() => setRetryConfirmOpen(true)}>
                  重试该用例
                </Button>
              ) : (
                <span className="muted small">{retryBlocked ?? "当前状态不可重试"}</span>
              )}
            </div>

            {detail.case.error_summary ? (
              <Alert
                type="error"
                showIcon
                message={detail.case.error_code ?? "失败"}
                description={detail.case.error_summary}
              />
            ) : null}

            <div className="case-drawer-section">
              <div className="case-drawer-title">Attempt 历史</div>
              <div className="attempt-list">
                {detail.attempts.map((attempt) => (
                  <button
                    type="button"
                    key={attempt.id}
                    className={`attempt-chip ${attempt.attempt_no === selectedAttempt ? "active" : ""}`}
                    onClick={() => onAttemptChange(attempt.attempt_no)}
                  >
                    <span>attempt {attempt.attempt_no}</span>
                    {statusTag(attempt.status, attemptStatusMeta)}
                    <span className="muted small">{formatDuration(attempt.duration_ms)}</span>
                  </button>
                ))}
                {detail.attempts.length === 0 ? <span className="muted small">没有 attempt 记录</span> : null}
              </div>
              {selectedSummary ? <TestSummaryChips summary={selectedSummary} /> : null}
            </div>

            {testsQuery.error ? (
              <QueryError error={testsQuery.error} onRetry={testsQuery.refetch} />
            ) : (
              <Table<CaseTestResult>
                rowKey="id"
                size="small"
                loading={testsQuery.isLoading}
                dataSource={testsQuery.data?.items ?? []}
                columns={testColumns}
                pagination={{ pageSize: 20, hideOnSinglePage: true }}
                expandable={{
                  rowExpandable: (record) => Boolean(record.failure_message || record.log_object_key),
                  expandedRowRender: (record) => (
                    <div className="case-log-panel">
                      {record.failure_message ? (
                        <pre className="eval-pre">{record.failure_message}</pre>
                      ) : (
                        <div className="muted small">该测试没有失败信息，仅保留对象引用。</div>
                      )}
                      {record.log_object_key ? (
                        <div className="case-log-object">
                          <span className="muted small">日志对象</span>
                          <Typography.Text className="mono small" copyable>
                            {record.log_object_key}
                          </Typography.Text>
                          <Button size="small" onClick={() => setLogRecord(record)}>
                            查看日志引用
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  ),
                }}
                locale={{
                  emptyText: (
                    <EmptyState
                      icon="terminal"
                      title={selectedAttempt ? "该 attempt 没有测试打点" : "没有可展示的 attempt"}
                      description="测试级打点在 Runner 上传评分报告后写入；未知类型会安全降级展示。"
                      compact
                    />
                  ),
                }}
              />
            )}

            <div className="case-drawer-section">
              <div className="case-drawer-title">轨迹与对象</div>
              <SummaryList
                items={[
                  ["轨迹状态", statusTag(detail.case.trace_status, traceStatusMeta)],
                  ["会话轨迹", detail.case.trace_status === "ready" ? "轨迹已就绪，可进入会话查看（M6 提供独立轨迹页）" : "轨迹尚未就绪或已降级"],
                  ["测试日志", testsQuery.data?.items.find((item) => item.log_object_key)?.log_object_key ?? "—"],
                  ["最近更新", formatTime(detail.case.updated_at)],
                ]}
              />
            </div>
          </div>
        )}
      </Drawer>

      <Modal
        open={retryConfirmOpen}
        title="重试该用例"
        okText="创建新 attempt"
        cancelText="取消"
        confirmLoading={retrying}
        onOk={() => {
          setRetryConfirmOpen(false);
          onRetry();
        }}
        onCancel={() => setRetryConfirmOpen(false)}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="重试只创建新 attempt，不删除旧证据"
          description="旧 attempt 的轨迹、测试打点与失败日志保留；新 attempt 会重新派发到资源池执行。"
        />
        <SummaryList
          items={[
            ["用例", detail?.case.case_key ?? "—"],
            ["当前 attempt", detail ? `${detail.case.latest_attempt_no}` : "—"],
            ["新 attempt", detail ? `${detail.case.latest_attempt_no + 1}` : "—"],
            ["attempt 上限", detail ? `${detail.run.max_attempts}` : "—"],
          ]}
        />
      </Modal>

      <Modal
        open={Boolean(logRecord)}
        title="失败日志引用"
        footer={<Button onClick={() => setLogRecord(null)}>关闭</Button>}
        onCancel={() => setLogRecord(null)}
      >
        {logRecord ? (
          <div className="case-log-modal">
            <div className="muted small">失败信息</div>
            <pre className="eval-pre">{logRecord.failure_message ?? "（没有内联失败信息）"}</pre>
            <div className="muted small">对象键</div>
            <Typography.Text className="mono small" copyable>
              {logRecord.log_object_key ?? "—"}
            </Typography.Text>
            <div className="muted small" style={{ marginTop: 12 }}>
              浏览器不允许直接读取对象存储；该引用用于在受权限控制的运维入口定位完整日志。
            </div>
          </div>
        ) : null}
      </Modal>
    </>
  );
}

import { useEffect, useMemo, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
} from "@tanstack/react-query";
import {
  Alert,
  App as AntApp,
  Button,
  Empty,
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
import { fetchEvaluationCase } from "../api/evalWorkbench";
import {
  createTraceFileDownloadUrl,
  createTraceSpanContentUrl,
  downloadTraceArchive,
  fetchAttemptTrace,
  fetchTraceSessionSpans,
  fetchTraceSpan,
  type TraceFile,
  type TraceDownloadUrl,
  type TraceSession,
  type TraceSpan,
} from "../api/traces";
import { useAuth } from "../app/auth";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import {
  buildArchiveTree,
  formatBytes,
  summarizeTraceExport,
} from "../features/traces/traceExportModel";
import {
  buildSpanWindows,
  flattenSessionTree,
  markMissingParents,
  mergeSelectedSpan,
  mergeSpanPages,
  readSpanFilter,
  readTraceSelection,
  spanBelongsToSession,
  traceStatusForAttempt,
} from "../features/traces/traceModel";

const FILTERS = [
  { value: "all", label: "全部" },
  { value: "AGENT", label: "Agent" },
  { value: "LLM", label: "LLM" },
  { value: "TOOL", label: "Tool" },
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

function durationLabel(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

function timeLabel(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function compactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}

function statusTag(status: string, meta = STATUS_META) {
  const item = meta[status] ?? { label: status || "未知", color: "default" };
  return <Tag color={item.color}>{item.label}</Tag>;
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

interface SessionTreeProps {
  linked: Array<{ session: TraceSession; depth: number }>;
  unlinked: Array<{ session: TraceSession; depth: number }>;
  selectedId: string | null;
  onSelect: (session: TraceSession) => void;
}

function SessionTree({ linked, unlinked, selectedId, onSelect }: SessionTreeProps) {
  const renderItem = (item: { session: TraceSession; depth: number }, group: "linked" | "unlinked") => (
    <button
      key={item.session.id}
      type="button"
      className={`trace-tree-item ${selectedId === item.session.id ? "active" : ""} ${
        item.depth ? `depth-${Math.min(item.depth, 3)}` : ""
      }`}
      onClick={() => onSelect(item.session)}
      aria-current={selectedId === item.session.id ? "true" : undefined}
    >
      <span className={`trace-session-mark ${group === "unlinked" ? "warn" : ""}`}>
        {group === "unlinked" ? "U" : "A"}
      </span>
      <span className="trace-tree-copy">
        <span className="truncate">{item.session.agent_name ?? item.session.id.slice(0, 12)}</span>
        <span className="trace-tree-meta">
          {item.session.agent_id ? `agent ${item.session.agent_id.slice(0, 10)}` : item.session.runtime_key}
        </span>
      </span>
      <span className="trace-tree-count">{item.session.span_count}</span>
    </button>
  );

  return (
    <div className="trace-session-tree">
      <div className="trace-pane-title">Session tree</div>
      {linked.map((item) => renderItem(item, "linked"))}
      {unlinked.length ? (
        <>
          <div className="trace-group-label">
            <Icon name="alert" size={12} /> Unlinked sessions
          </div>
          {unlinked.map((item) => renderItem(item, "unlinked"))}
        </>
      ) : null}
    </div>
  );
}

interface WaterfallProps {
  spans: TraceSpan[];
  selectedSpanId: string | null;
  filter: string;
  clockComparable: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onFilter: (value: string) => void;
  onSelect: (span: TraceSpan) => void;
  onLoadMore: () => void;
}

function Waterfall({
  spans,
  selectedSpanId,
  filter,
  clockComparable,
  hasMore,
  loadingMore,
  onFilter,
  onSelect,
  onLoadMore,
}: WaterfallProps) {
  const windows = useMemo(() => buildSpanWindows(spans, clockComparable), [spans, clockComparable]);
  const ids = useMemo(() => new Set(spans.map((item) => item.id)), [spans]);
  const byId = useMemo(() => new Map(spans.map((item) => [item.id, item])), [spans]);
  const depthById = useMemo(() => {
    const result = new Map<string, number>();
    const resolveDepth = (span: TraceSpan, seen = new Set<string>()): number => {
      if (result.has(span.id)) return result.get(span.id) as number;
      if (!span.parent_span_id || !ids.has(span.parent_span_id) || seen.has(span.id)) {
        result.set(span.id, 0);
        return 0;
      }
      const parent = byId.get(span.parent_span_id);
      seen.add(span.id);
      const depth = parent ? Math.min(3, resolveDepth(parent, seen) + 1) : 0;
      result.set(span.id, depth);
      return depth;
    };
    for (const span of spans) resolveDepth(span);
    return result;
  }, [byId, ids, spans]);
  const counts = useMemo(() => {
    const result: Record<string, number> = { all: spans.length };
    for (const span of spans) result[span.kind] = (result[span.kind] ?? 0) + 1;
    return result;
  }, [spans]);

  return (
    <section className="trace-timeline" aria-label="Span waterfall">
      <div className="trace-toolbar">
        <span className="trace-warning-count">
          <Icon name="alert" size={13} />
          {spans.filter((item) => item.parent_missing).length} 个根片段
        </span>
        <Segmented
          size="small"
          value={filter}
          options={FILTERS.map((item) => ({
            value: item.value,
            label: (
              <span>
                {item.label}
                <span className="trace-filter-count">
                  {item.value === "all" ? counts.all : (counts[item.value] ?? 0)}
                </span>
              </span>
            ),
          }))}
          onChange={(value) => onFilter(String(value))}
        />
      </div>
      {!clockComparable ? (
        <div className="trace-clock-warning">
          <Icon name="clock" size={13} />
          时钟不可比：waterfall 按原生 ordinal 顺序展示，不推断并行时间关系。
        </div>
      ) : null}
      <div className="waterfall-head" aria-hidden="true">
        <span>Span</span>
        <span>{windows.mode === "time" ? "Timeline" : "Ordinal timeline"}</span>
        <span>Duration</span>
      </div>
      <div className="trace-waterfall-rows">
        {windows.rows.map((row) => {
          const span = row.span;
          const type = span.kind.toLowerCase();
          return (
            <button
              key={span.id}
              type="button"
              className={`trace-waterfall-row ${selectedSpanId === span.id ? "selected" : ""}`}
              onClick={() => onSelect(span)}
              aria-current={selectedSpanId === span.id ? "true" : undefined}
            >
              <span
                className={`trace-span-name depth-${depthById.get(span.id) ?? 0}`}
                title={span.name ?? span.id}
              >
                <span className={`trace-span-kind ${type}`}>{span.kind.slice(0, 1)}</span>
                <span className="trace-span-label-wrap">
                  <span className="trace-span-label truncate">{span.name ?? span.id}</span>
                  <span className="trace-span-kind-label">
                    {span.kind}
                    {span.parent_missing ? " · root fragment" : ""}
                  </span>
                </span>
              </span>
              <span className="trace-waterfall-track">
                <span
                  className={`trace-waterfall-bar ${type} ${
                    span.status && /fail|error/i.test(span.status) ? "error" : ""
                  }`}
                  style={{
                    left: `${row.leftPercent}%`,
                    width: `${row.widthPercent}%`,
                  }}
                />
              </span>
              <span className="trace-span-duration">{durationLabel(row.durationMs)}</span>
            </button>
          );
        })}
        {spans.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="该 session 没有可展示的 span"
          />
        ) : null}
      </div>
      {hasMore ? (
        <div className="trace-load-more">
          <Button loading={loadingMore} onClick={onLoadMore}>
            加载更多 span
          </Button>
        </div>
      ) : null}
    </section>
  );
}

interface InspectorProps {
  span: TraceSpan | null;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  onOpenContent: (span: TraceSpan) => void;
  openingContent: boolean;
}

function Inspector({
  span,
  loading,
  error,
  onRetry,
  onOpenContent,
  openingContent,
}: InspectorProps) {
  if (loading) {
    return (
      <aside className="trace-inspector">
        <Skeleton active paragraph={{ rows: 10 }} />
      </aside>
    );
  }
  if (error) {
    return (
      <aside className="trace-inspector trace-inspector-state">
        <QueryError
          error={error}
          onRetry={onRetry}
          title="无法加载 span 详情"
          endpoint="GET /api/v1/trace-spans/:id"
        />
      </aside>
    );
  }
  if (!span) {
    return (
      <aside className="trace-inspector trace-inspector-state">
        <EmptyState
          icon="list"
          title="选择一个 span"
          description="从左侧选择 session，再在 waterfall 中选择 span 查看语义属性、消息预览和受控原始记录。"
          compact
        />
      </aside>
    );
  }

  const attributes = Object.entries(span.attributes);
  const contents = (
    <div className="trace-inspector-scroll">
      {span.content_hidden ? (
        <Alert
          type="warning"
          showIcon
          message="完整 thinking 已按权限隐藏"
          description="只读角色只能看到推理段的类型、时长和 token；需要管理员权限才能访问完整内容。"
        />
      ) : (
        <>
          <div className="trace-detail-section-title">Content preview</div>
          {span.content_preview ? (
            <pre className="trace-content-pre">{span.content_preview}</pre>
          ) : (
            <div className="trace-muted-block">该 span 没有沉淀内容预览。</div>
          )}
          {span.content_available ? (
            <Button
              size="small"
              loading={openingContent}
              icon={<Icon name="file" size={13} />}
              onClick={() => onOpenContent(span)}
            >
              按需获取完整内容
            </Button>
          ) : (
            <div className="small muted">完整正文未外置；当前仅保留脱敏预览。</div>
          )}
        </>
      )}
    </div>
  );
  const raw = (
    <div className="trace-inspector-scroll">
      <div className="trace-detail-section-title">Normalized record</div>
      <pre className="trace-content-pre">{JSON.stringify(span, null, 2)}</pre>
      <Alert
        type="info"
        showIcon
        message="原件下载受管理员权限和审计控制"
        description="原始对象包含完整会话内容，不通过公开 object key 暴露。请从左侧 Raw source 获取短时地址。"
      />
    </div>
  );

  return (
    <aside className="trace-inspector" aria-label="Span inspector">
      <div className="trace-inspector-head">
        <div className="row-between">
          <Tag color="blue">{span.kind}</Tag>
          <Tooltip title="复制 span ID">
            <Button
              type="text"
              size="small"
              aria-label="复制 span ID"
              onClick={() => void navigator.clipboard?.writeText(span.id)}
            >
              <Icon name="list" size={13} />
            </Button>
          </Tooltip>
        </div>
        <div className="trace-inspector-title">{span.name ?? span.id}</div>
        <div className="trace-inspector-id mono">{span.id}</div>
      </div>
      <Tabs
        size="small"
        items={[
          {
            key: "attributes",
            label: "ATTRIBUTES",
            children: (
              <div className="trace-inspector-scroll">
                {span.parent_missing ? (
                  <Alert
                    type="warning"
                    showIcon
                    message="Root fragment"
                    description="父 span 不在当前 session 或已丢失，按 root fragment 展示。"
                  />
                ) : null}
                <div className="trace-detail-section-title">Semantic attributes</div>
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
                    <span className="trace-attr-value">{timeLabel(span.started_at)}</span>
                  </div>
                  <div className="trace-attr-row">
                    <span className="trace-attr-key">ended_at</span>
                    <span className="trace-attr-value">{timeLabel(span.ended_at)}</span>
                  </div>
                  <div className="trace-attr-row">
                    <span className="trace-attr-key">status</span>
                    <span className="trace-attr-value">{span.status ?? "—"}</span>
                  </div>
                  <div className="trace-attr-row">
                    <span className="trace-attr-key">usage</span>
                    <span className="trace-attr-value">
                      {compactNumber(span.input_tokens)} in · {compactNumber(span.output_tokens)} out
                    </span>
                  </div>
                </div>
              </div>
            ),
          },
          { key: "input", label: "INPUT", children: contents },
          { key: "output", label: "OUTPUT", children: contents },
          { key: "raw", label: "RAW", children: raw },
        ]}
      />
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
  const [signedLink, setSignedLink] = useState<
    (TraceDownloadUrl & { label: string }) | null
  >(null);
  const [exportOpen, setExportOpen] = useState(false);
  const filter = readSpanFilter(searchParams.toString());

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
    queryFn: ({ signal }) => fetchAttemptTrace(caseRunId, selection.attempt, signal),
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
    queryKey: ["trace-spans", caseRunId, attempt?.id, selectedSessionId, filter],
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
  const visibleSpan =
    spans.find((item) => item.id === selection.span) ??
    (selection.span === null ? (spans[0] ?? null) : null);

  useEffect(() => {
    if (!selectedSessionId || selection.span !== null || !spans[0]) return;
    updateSelection({ span: spans[0].id }, true);
  }, [selectedSessionId, selection.span, spans]);

  const selectedSpanId = visibleSpan?.id ?? selection.span;
  const detailQuery = useQuery({
    queryKey: ["trace-span", caseRunId, attempt?.id, selectedSessionId, selectedSpanId],
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

  const span = detailSpan ?? visibleSpan;
  const waterfallSpans = useMemo(() => {
    const merged = mergeSelectedSpan(spans, span, selectedSessionId, filter);
    if (!span?.parent_missing) return merged;
    return merged.map((item) =>
      item.id === span.id ? { ...item, parent_missing: true } : item,
    );
  }, [filter, selectedSessionId, span, spans]);

  const fileDownload = useMutation({
    mutationFn: (fileId: string) => createTraceFileDownloadUrl(fileId),
    onSuccess: (result) => {
      setSignedLink({ ...result, label: "轨迹原件" });
      message.success(`原件地址已签发，${timeLabel(result.expiresAt)} 过期`);
    },
    onError: (error) =>
      message.error(error instanceof Error ? error.message : "原件下载地址签发失败"),
  });
  const contentDownload = useMutation({
    mutationFn: (spanId: string) => createTraceSpanContentUrl(spanId),
    onSuccess: (result) => {
      setSignedLink({ ...result, label: "完整 span 内容" });
      message.success("完整内容地址已签发");
    },
    onError: (error) =>
      message.error(error instanceof Error ? error.message : "完整内容地址签发失败"),
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
    updateSelection({ attempt: attemptNo, session: null, span: null, kind: null });
  };
  const selectSession = (session: TraceSession) => {
    setSignedLink(null);
    updateSelection({ session: session.id, span: null, kind: null });
  };
  const selectSpan = (spanItem: TraceSpan) => {
    setSignedLink(null);
    updateSelection({ session: spanItem.session_id, span: spanItem.id });
  };
  const changeFilter = (value: string) => {
    setSignedLink(null);
    updateSelection({ span: null, kind: value === "all" ? null : value });
  };

  if (!caseRunId) {
    return (
      <section className="page trace-page">
        <EmptyState icon="search" title="缺少用例标识" description="请从评测用例明细进入轨迹页。" />
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
      ? Date.parse(trace.attempt.ended_at) - Date.parse(trace.attempt.started_at)
      : null;
  const attempts = caseQuery.data?.attempts ?? (trace.attempt ? [trace.attempt] : []);
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
    unlinkedCount: exportSummary.unlinkedCount,
  });
  const sessionSpanTotal = spansQuery.data?.pages[0]?.pageInfo.total ?? selectedSession?.span_count ?? 0;

  return (
    <section className="page trace-page">
      <div className="trace-back-row">
        <Button
          type="text"
          size="small"
          icon={<Icon name="arrowLeft" size={13} />}
          onClick={() => navigate(`/evals/${encodeURIComponent(runId)}?tab=cases&case=${encodeURIComponent(caseRunId)}`)}
        >
          返回评测详情
        </Button>
      </div>
      <PageHeading
        eyebrow="Session trace"
        title={caseQuery.data?.case.case_key ?? attemptQuery.data.caseRunId}
        description={`${attempt?.status ?? "unknown"} · ${trace.parserVersions.join(", ") || "parser pending"} · 主 Agent 与子 Agent 使用统一父子证据展示`}
        actions={
          <>
            <Select
              value={attempt?.attempt_no}
              style={{ width: 150 }}
              aria-label="选择 attempt"
              options={attempts.map((item) => ({
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
            .map((item) => item.error_summary ?? item.error_code ?? item.logical_path)
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
          description="部分 span 起止时间倒置或缺失，waterfall 已退回原生顺序布局。"
        />
      ) : null}

      <section className="trace-shell">
        <div className="trace-top">
          <Tag color="purple">Claude Code</Tag>
          <Tag color="blue">{selectedSession?.parser_version ?? "parser pending"}</Tag>
          {statusTag(attemptTraceStatus)}
          <span className="small muted mono">
            {attempt?.id ?? "尚未生成 attempt"}
          </span>
          <div className="trace-summary" aria-label="轨迹摘要">
            <span><strong>{durationLabel(totalDuration)}</strong> 总耗时</span>
            <span><strong>{compactNumber(trace.stats.inputTokens + trace.stats.outputTokens)}</strong> tokens</span>
            <span><strong>{trace.stats.sessionCount}</strong> sessions</span>
            <span><strong>{sessionSpanTotal || trace.stats.spanCount}</strong> spans</span>
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
          />
        </div>

        <div className="trace-layout">
          <aside className="trace-tree-pane">
            <SessionTree
              linked={flat.linked}
              unlinked={flat.unlinked}
              selectedId={selectedSessionId}
              onSelect={selectSession}
            />
            <div className="trace-raw-source">
              <div className="trace-pane-title">Raw source</div>
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
                    <span className="truncate mono">{file.logical_path ?? file.id}</span>
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
          </aside>

          {spansQuery.error ? (
            <section className="trace-timeline trace-timeline-error">
              <QueryError
                error={spansQuery.error}
                onRetry={() => spansQuery.refetch()}
                title="无法加载 session spans"
                endpoint="GET /api/v1/trace-sessions/:id/spans"
              />
            </section>
          ) : spansQuery.isLoading ? (
            <section className="trace-timeline trace-timeline-loading">
              <Spin tip="正在加载 session spans"><div className="trace-spin-space" /></Spin>
            </section>
          ) : (
            <Waterfall
              spans={waterfallSpans}
              selectedSpanId={selectedSpanId}
              filter={filter}
              clockComparable={trace.quality.clock_comparable}
              hasMore={Boolean(spansQuery.hasNextPage)}
              loadingMore={spansQuery.isFetchingNextPage}
              onFilter={changeFilter}
              onSelect={selectSpan}
              onLoadMore={() => void spansQuery.fetchNextPage()}
            />
          )}

          <Inspector
            span={span ?? null}
            loading={detailQuery.isLoading}
            error={detailQuery.error}
            onRetry={() => detailQuery.refetch()}
            onOpenContent={(item) => contentDownload.mutate(item.id)}
            openingContent={contentDownload.isPending}
          />
        </div>
      </section>
      <Modal
        open={Boolean(signedLink)}
        title="受控地址已签发"
        onCancel={() => setSignedLink(null)}
        footer={[
          <Button key="close" onClick={() => setSignedLink(null)}>关闭</Button>,
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

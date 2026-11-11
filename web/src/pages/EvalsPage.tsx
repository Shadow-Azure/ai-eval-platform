import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Drawer, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Timeline, message } from "antd";
import type { TableColumnsType } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { StatusCard } from "../components/StatusCard";
import { fetchDatasets } from "../api/datasets";
import {
  TERMINAL_EVAL_STATUSES,
  cancelEvaluation,
  createEvaluation,
  fetchEvaluationDetail,
  fetchEvaluationEvents,
  fetchEvaluations,
  type EvaluationCase,
  type EvaluationEvent,
  type EvaluationListQuery,
  type EvaluationShard,
  type EvaluationSummary,
} from "../api/evaluations";
import { EVAL_STATUS_OPTIONS, evalStatusMeta } from "../utils/evalStatus";

const evaluationsQueries = {
  all: ["evaluations"] as const,
  list: (filters: EvaluationListQuery) => ["evaluations", "list", filters] as const,
  detail: ["evaluations", "detail"] as const,
  events: ["evaluations", "events"] as const,
};

const caseStatusLabels: Record<string, string> = {
  queued: "排队",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  timeout: "超时",
  cancelled: "已取消",
};

const eventTypeLabels: Record<string, string> = {
  "eval.created": "评测已创建",
  "eval.dispatched": "分片已派发 GitHub",
  "eval.dispatch_failed": "分片派发失败",
  "eval.cancelled": "评测已取消",
  "eval.budget_pausing": "预算用尽，暂停收口",
  "eval.budget_paused": "评测进入预算暂停",
  "eval.budget_resumed": "增加预算并恢复调度",
  "case.started": "用例开始执行",
  "case.heartbeat": "用例心跳",
  "case.uploaded": "对象上传清单",
  "case.completed": "用例完成",
  "case.failed": "用例失败",
  "case.retry_queued": "重试已排队",
  "case.artifact_missing": "结果对象缺失",
  "case.started_late": "迟到 started（保留审计）",
  "case.started_rejected": "启动被拒绝（预算暂停 / 已取消）",
};

function StatusTag({ status }: { status: string }) {
  const meta = evalStatusMeta(status);
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function timeLabel(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString("zh-CN", { hour12: false });
}

function dateTimeLabel(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const now = new Date();
  const time = parsed.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const sameDay =
    parsed.getFullYear() === now.getFullYear() &&
    parsed.getMonth() === now.getMonth() &&
    parsed.getDate() === now.getDate();
  if (sameDay) return `今天 ${time}`;
  return `${parsed.getMonth() + 1} 月 ${parsed.getDate()} 日 ${time}`;
}

const RUNTIME_LABELS: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex CLI",
  opencode: "OpenCode",
  pi: "pi",
};

function runtimeLabel(key: string | null | undefined): string {
  if (!key) return "—";
  return RUNTIME_LABELS[key] ?? key;
}

function durationLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return "—";
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分`;
  return `${Math.max(1, Math.round(seconds))} 秒`;
}

function durationCompact(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return "—";
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${Math.max(1, Math.round(seconds))}s`;
}

function progressPercent(row: EvaluationSummary): number {
  if (!row.total_cases) return 0;
  const finished = row.finished_count ?? Math.max(0, row.total_cases - row.queued_count - row.running_count);
  return Math.min(100, Math.round((finished / row.total_cases) * 100));
}

function rateLabel(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)}%`;
}

const caseColumns: TableColumnsType<EvaluationCase> = [
  { title: "#", dataIndex: "ordinal", key: "ordinal", width: 48 },
  { title: "用例", dataIndex: "case_key", key: "case_key" },
  {
    title: "执行状态",
    dataIndex: "execution_status",
    key: "execution_status",
    render: (value: string) => caseStatusLabels[value] ?? value,
  },
  { title: "结果", dataIndex: "result_status", key: "result_status" },
  { title: "最新 attempt", dataIndex: "latest_attempt_no", key: "latest_attempt_no", width: 96 },
  {
    title: "错误",
    dataIndex: "error_code",
    key: "error_code",
    render: (value: string | null) => value ?? "—",
  },
];

const shardColumns: TableColumnsType<EvaluationShard> = [
  { title: "分片", dataIndex: "shard_id", key: "shard_id" },
  { title: "attempt", dataIndex: "attempt", key: "attempt", width: 72 },
  {
    title: "outbox",
    dataIndex: "status",
    key: "status",
    render: (value: string) => <Tag>{value}</Tag>,
  },
  {
    title: "GitHub",
    dataIndex: "github_status",
    key: "github_status",
    render: (value: number | null) => (value ? `HTTP ${value}` : "—"),
  },
  {
    title: "派发时间",
    dataIndex: "dispatched_at",
    key: "dispatched_at",
    render: (value: string | null) => (value ? timeLabel(value) : "—"),
  },
  {
    title: "最近错误",
    dataIndex: "last_error",
    key: "last_error",
    ellipsis: true,
    render: (value: string | null) => value ?? "—",
  },
];

function eventLabel(event: EvaluationEvent): string {
  const base = eventTypeLabels[event.event_type] ?? event.event_type;
  const caseSuffix = event.case_run_id ? ` · ${event.case_run_id.slice(0, 8)}` : "";
  return `${base}${caseSuffix}`;
}

function eventDescription(event: EvaluationEvent): string {
  const parts: string[] = [];
  if (event.attempt_no !== null) parts.push(`attempt ${event.attempt_no}`);
  const payload = event.payload;
  if (typeof payload.error === "string") parts.push(String(payload.error).slice(0, 120));
  if (typeof payload.dispatch_key === "string") parts.push(String(payload.dispatch_key));
  if (typeof payload.new_attempt_no === "number") parts.push(`新 attempt ${payload.new_attempt_no}`);
  return parts.join(" · ");
}

interface CreateFormValues {
  name: string;
  case_count: number;
}

export function EvalsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [detailId, setDetailId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<CreateFormValues>();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [datasetFilter, setDatasetFilter] = useState("all");

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const listQuery = useQuery({
    queryKey: evaluationsQueries.list({
      q: debouncedSearch,
      status: statusFilter,
      datasetId: datasetFilter,
    }),
    queryFn: ({ signal }) =>
      fetchEvaluations(
        {
          q: debouncedSearch || undefined,
          status: statusFilter === "all" ? undefined : statusFilter,
          datasetId: datasetFilter === "all" ? undefined : datasetFilter,
        },
        signal,
      ),
    refetchInterval: 3000,
  });

  const datasetsQuery = useQuery({
    queryKey: ["datasets", "eval-filter"],
    queryFn: ({ signal }) => fetchDatasets(signal),
    staleTime: 60_000,
  });

  const detailQuery = useQuery({
    queryKey: [...evaluationsQueries.detail, detailId],
    queryFn: ({ signal }) => fetchEvaluationDetail(detailId as string, signal),
    enabled: detailId !== null,
    refetchInterval: (query) =>
      query.state.data && !TERMINAL_EVAL_STATUSES.includes(query.state.data.status) ? 2000 : false,
  });

  const runId = detailQuery.data?.id ?? null;
  const eventsQuery = useQuery({
    queryKey: [...evaluationsQueries.events, runId],
    queryFn: ({ signal }) => fetchEvaluationEvents(runId as string, {}, signal),
    enabled: runId !== null,
    refetchInterval: 3000,
  });

  const createMutation = useMutation({
    mutationFn: async (values: CreateFormValues) => {
      const caseKeys = Array.from({ length: values.case_count }, (_, index) => `synthetic-case-${index + 1}`);
      return createEvaluation({
        name: values.name,
        case_keys: caseKeys,
        idempotency_key: `web-${Date.now()}`,
      });
    },
    onSuccess: (created) => {
      message.success(`评测已创建：${created.id.slice(0, 8)}`);
      setCreateOpen(false);
      form.resetFields();
      void queryClient.invalidateQueries({ queryKey: evaluationsQueries.all });
      setDetailId(created.id);
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "创建失败"),
  });

  const cancelMutation = useMutation({
    mutationFn: (runId: string) => cancelEvaluation(runId),
    onSuccess: () => {
      message.success("已取消");
      void queryClient.invalidateQueries({ queryKey: evaluationsQueries.all });
      if (runId) void queryClient.invalidateQueries({ queryKey: [...evaluationsQueries.detail, runId] });
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "取消失败"),
  });

  const stats = listQuery.data?.stats;
  const hasFilters = Boolean(debouncedSearch) || statusFilter !== "all" || datasetFilter !== "all";
  const statsLoading = listQuery.isLoading && !listQuery.data;
  const datasetOptions = [
    { value: "all", label: "数据集：全部" },
    ...(datasetsQuery.data ?? []).map((dataset) => ({
      value: dataset.id,
      label: `数据集：${dataset.name}`,
    })),
  ];
  const statusOptions = [
    { value: "all", label: "状态：全部" },
    ...EVAL_STATUS_OPTIONS.map((option) => ({
      value: option.value,
      label: `状态：${option.label}`,
    })),
  ];

  const resetFilters = () => {
    setSearch("");
    setDebouncedSearch("");
    setStatusFilter("all");
    setDatasetFilter("all");
  };

  const columns: TableColumnsType<EvaluationSummary> = [
    {
      title: "评测任务",
      dataIndex: "name",
      key: "name",
      render: (_value, row) => (
        <div>
          <div className="table-primary">{row.name}</div>
          <div className="table-secondary">
            {row.id.slice(0, 8)}
            {row.resource_pool_id ? ` · ${row.resource_pool_id.slice(0, 8)}` : ""}
          </div>
        </div>
      ),
    },
    {
      title: "Agent / 模型",
      key: "agent",
      render: (_value, row) => (
        <div>
          <div>{runtimeLabel(row.runtime_key)}</div>
          <div className="table-secondary">{row.model_name ?? "—"}</div>
        </div>
      ),
    },
    {
      title: "数据集",
      key: "dataset",
      render: (_value, row) => (
        <div>
          <div>{row.dataset_name ?? "未绑定数据集"}</div>
          <div className="table-secondary">{row.dataset_version ?? "—"}</div>
        </div>
      ),
    },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <StatusTag status={value} /> },
    {
      title: "进展",
      key: "progress",
      render: (_value, row) => {
        const percent = progressPercent(row);
        return (
          <div className="table-progress">
            <div className="progress">
              <span style={{ width: `${percent}%` }} />
            </div>
            <span>{percent}%</span>
          </div>
        );
      },
    },
    {
      title: "核心指标",
      key: "metric",
      render: (_value, row) => (
        <div>
          <strong>{rateLabel(row.resolve_rate)}</strong>
          <div className="table-secondary">解决率</div>
        </div>
      ),
    },
    {
      title: "开始时间",
      key: "started_at",
      render: (_value, row) => (
        <div>
          <div>{dateTimeLabel(row.started_at)}</div>
          <div className="table-secondary">{durationLabel(row.duration_seconds)}</div>
        </div>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_value, row) => (
        <Space>
          <Button type="link" size="small" onClick={() => setDetailId(row.id)}>
            详情
          </Button>
          {!TERMINAL_EVAL_STATUSES.includes(row.status) && (
            <Button
              type="link"
              size="small"
              danger
              loading={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate(row.id)}
            >
              取消
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const detail = detailQuery.data;

  return (
    <div className="page">
      <PageHeading
        eyebrow="Evaluation · Eval runs"
        title="评测任务"
        description="一次评测由数据集版本、Agent 运行时、Git 资产源、模型凭据与资源池共同定义。"
        actions={
          <>
            <Button icon={<Icon name="refresh" size={14} />} onClick={() => listQuery.refetch()}>
              刷新
            </Button>
            <Button type="primary" icon={<Icon name="plus" size={14} />} onClick={() => navigate("/evals/new")}>
              发起评测
            </Button>
          </>
        }
      />

      {listQuery.isError && !listQuery.data ? (
        <QueryError
          error={listQuery.error}
          onRetry={() => listQuery.refetch()}
          title="无法加载评测列表"
          endpoint="GET /api/v1/evaluations"
        />
      ) : (
        <>
          <div className="grid grid-4">
            <StatusCard
              label="全部任务"
              value={stats?.total ?? 0}
              icon="play"
              meta={<span>筛选范围内</span>}
              loading={statsLoading}
            />
            <StatusCard
              label="运行中"
              value={stats?.active ?? 0}
              icon="cpu"
              tone="success"
              meta={<span>{stats?.running_cases ?? 0} 个用例正在执行</span>}
              loading={statsLoading}
            />
            <StatusCard
              label="平均解决率"
              value={rateLabel(stats?.avg_resolve_rate)}
              icon="sparkles"
              tone="violet"
              meta={
                <span>
                  {stats && stats.scored_cases > 0
                    ? `${stats.resolved_cases} / ${stats.scored_cases} 已评分`
                    : "暂无已评分用例"}
                </span>
              }
              loading={statsLoading}
            />
            <StatusCard
              label="平均耗时"
              value={durationCompact(stats?.avg_duration_seconds)}
              icon="clock"
              tone="warning"
              meta={<span>按终态任务平均</span>}
              loading={statsLoading}
            />
          </div>

          <section className="card section-gap">
            <div className="eval-filter-bar">
              <Input
                allowClear
                aria-label="搜索评测"
                placeholder="搜索评测名称、Agent 或模型"
                prefix={<Icon name="search" size={14} />}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                style={{ width: 270 }}
              />
              <Select
                aria-label="状态筛选"
                value={statusFilter}
                options={statusOptions}
                onChange={(value) => setStatusFilter(value)}
                style={{ width: 170 }}
              />
              <Select
                aria-label="数据集筛选"
                value={datasetFilter}
                options={datasetOptions}
                onChange={(value) => setDatasetFilter(value)}
                loading={datasetsQuery.isLoading}
                style={{ width: 260 }}
              />
              <span className="small muted" style={{ marginLeft: "auto" }}>
                {listQuery.data?.total ?? 0} 个任务 · 自动刷新
              </span>
              <Button type="link" size="small" onClick={() => setCreateOpen(true)}>
                合成评测
              </Button>
            </div>
            <Table
              rowKey="id"
              size="small"
              loading={listQuery.isLoading}
              columns={columns}
              dataSource={listQuery.data?.items ?? []}
              pagination={false}
              locale={{
                emptyText: hasFilters ? (
                  <EmptyState
                    icon="search"
                    title="没有符合条件的评测"
                    description="调整搜索词、状态或数据集筛选后再试。"
                    action={
                      <Button size="small" onClick={resetFilters}>
                        清除筛选
                      </Button>
                    }
                  />
                ) : (
                  <EmptyState
                    icon="list"
                    title="暂无评测任务"
                    description="发起一次评测后，这里会显示进度、解决率与耗时。"
                    action={
                      <Button size="small" type="primary" onClick={() => navigate("/evals/new")}>
                        发起评测
                      </Button>
                    }
                  />
                ),
              }}
            />
          </section>
        </>
      )}

      <Drawer
        title={detail ? `${detail.name} · ${detail.id.slice(0, 8)}` : "评测详情"}
        open={detailId !== null}
        width={720}
        onClose={() => setDetailId(null)}
        extra={detail ? <StatusTag status={detail.status} /> : null}
      >
        {detailQuery.isError ? (
          <QueryError
            error={detailQuery.error}
            onRetry={() => detailQuery.refetch()}
            title="无法加载评测详情"
            endpoint="GET /api/v1/evaluations/{id}"
          />
        ) : detail ? (
          <>
            <Space size="large" wrap style={{ marginBottom: 16 }}>
              <span>用例 {detail.total_cases}</span>
              <span>成功 {detail.succeeded_count}</span>
              <span>失败 {detail.failed_count}</span>
              <span>运行 {detail.running_count}</span>
              <span>排队 {detail.queued_count}</span>
              {detail.finished_at && <span>终态 {timeLabel(detail.finished_at)}</span>}
            </Space>

            <h4>分片派发对账</h4>
            <Table rowKey="dispatch_key" size="small" columns={shardColumns} dataSource={detail.shards} pagination={false} />

            <h4 style={{ marginTop: 16 }}>用例</h4>
            <Table rowKey="id" size="small" columns={caseColumns} dataSource={detail.cases} pagination={false} />

            <h4 style={{ marginTop: 16 }}>事件时间线</h4>
            {eventsQuery.isError ? (
              <QueryError
                error={eventsQuery.error}
                onRetry={() => eventsQuery.refetch()}
                title="无法加载事件"
                endpoint="GET /api/v1/evaluations/{id}/events"
              />
            ) : (
              <Timeline
                items={(eventsQuery.data?.items ?? []).map((event) => ({
                  key: event.id,
                  children: (
                    <div>
                      <div>{eventLabel(event)}</div>
                      {eventDescription(event) && (
                        <div className="table-secondary">{eventDescription(event)}</div>
                      )}
                      <div className="table-secondary">{timeLabel(event.occurred_at)}</div>
                    </div>
                  ),
                }))}
              />
            )}
          </>
        ) : (
          <p className="table-secondary">加载中…</p>
        )}
      </Drawer>

      <Modal
        title="发起合成评测"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
        okText="创建并派发"
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ name: "合成评测", case_count: 2 }}
          onFinish={(values) => createMutation.mutate(values)}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item
            name="case_count"
            label="合成用例数"
            rules={[{ required: true, message: "请输入用例数" }]}
            extra="不调用任何模型：用例在固定 Runner 上执行确定性合成任务并经 OSS 回传。"
          >
            <InputNumber min={1} max={256} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

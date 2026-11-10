import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Drawer, Form, Input, InputNumber, Modal, Space, Table, Tag, Timeline, message } from "antd";
import type { TableColumnsType } from "antd";
import { useState } from "react";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import {
  TERMINAL_EVAL_STATUSES,
  cancelEvaluation,
  createEvaluation,
  fetchEvaluationDetail,
  fetchEvaluationEvents,
  fetchEvaluations,
  type EvaluationCase,
  type EvaluationEvent,
  type EvaluationShard,
  type EvaluationSummary,
} from "../api/evaluations";

const evaluationsQueries = {
  list: ["evaluations"] as const,
  detail: ["evaluations", "detail"] as const,
  events: ["evaluations", "events"] as const,
};

const statusMeta: Record<string, { label: string; color: string }> = {
  queued: { label: "排队中", color: "default" },
  running: { label: "运行中", color: "processing" },
  completed: { label: "已完成", color: "success" },
  partial: { label: "部分成功", color: "warning" },
  failed: { label: "失败", color: "error" },
  cancelled: { label: "已取消", color: "default" },
  pausing_budget: { label: "预算暂停中", color: "warning" },
  paused_budget: { label: "预算已暂停", color: "warning" },
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
  const meta = statusMeta[status] ?? { label: status, color: "default" };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function timeLabel(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString("zh-CN", { hour12: false });
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
  const queryClient = useQueryClient();
  const [detailId, setDetailId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<CreateFormValues>();

  const listQuery = useQuery({
    queryKey: evaluationsQueries.list,
    queryFn: ({ signal }) => fetchEvaluations(signal),
    refetchInterval: 3000,
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
      void queryClient.invalidateQueries({ queryKey: evaluationsQueries.list });
      setDetailId(created.id);
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "创建失败"),
  });

  const cancelMutation = useMutation({
    mutationFn: (runId: string) => cancelEvaluation(runId),
    onSuccess: () => {
      message.success("已取消");
      void queryClient.invalidateQueries({ queryKey: evaluationsQueries.list });
      if (runId) void queryClient.invalidateQueries({ queryKey: [...evaluationsQueries.detail, runId] });
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "取消失败"),
  });

  const columns: TableColumnsType<EvaluationSummary> = [
    {
      title: "评测任务",
      dataIndex: "name",
      key: "name",
      render: (_value, row) => (
        <div>
          <div className="table-primary">{row.name}</div>
          <div className="table-secondary">{row.id.slice(0, 8)}</div>
        </div>
      ),
    },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <StatusTag status={value} /> },
    {
      title: "进展",
      key: "progress",
      render: (_value, row) => {
        const total = Math.max(1, row.total_cases);
        const done = row.succeeded_count + row.failed_count;
        const percent = Math.min(100, Math.round((done / total) * 100));
        return (
          <div className="table-progress">
            <div className="progress">
              <span style={{ width: `${percent}%` }} />
            </div>
            <span>
              {row.succeeded_count}✓ {row.failed_count}✗ {row.running_count}● {row.queued_count}○
            </span>
          </div>
        );
      },
    },
    {
      title: "创建",
      dataIndex: "created_at",
      key: "created_at",
      render: (value: string | null) => timeLabel(value),
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
        description="最小合成评测视图：从创建、GitHub 派发到 Runner 回调与终态持久化的全过程。"
        actions={
          <>
            <Button icon={<Icon name="database" size={14} />} onClick={() => listQuery.refetch()}>
              刷新
            </Button>
            <Button type="primary" icon={<Icon name="plus" size={14} />} onClick={() => setCreateOpen(true)}>
              发起合成评测
            </Button>
          </>
        }
      />

      {listQuery.isError ? (
        <QueryError error={listQuery.error} onRetry={() => listQuery.refetch()} title="无法加载评测列表" endpoint="GET /api/v1/evaluations" />
      ) : (
        <section className="card">
          <div className="card-header">
            <div>
              <h2>评测列表</h2>
              <p className="table-secondary">共 {listQuery.data?.total ?? 0} 条 · 每 3 秒自动刷新</p>
            </div>
          </div>
          <div className="card-body">
            <Table
              rowKey="id"
              size="small"
              loading={listQuery.isLoading}
              columns={columns}
              dataSource={listQuery.data?.items ?? []}
              pagination={false}
            />
          </div>
        </section>
      )}

      <Drawer
        title={detail ? `${detail.name} · ${detail.id.slice(0, 8)}` : "评测详情"}
        open={detailId !== null}
        width={720}
        onClose={() => setDetailId(null)}
        extra={detail ? <StatusTag status={detail.status} /> : null}
      >
        {detailQuery.isError ? (
          <QueryError error={detailQuery.error} onRetry={() => detailQuery.refetch()} title="无法加载评测详情" endpoint="GET /api/v1/evaluations/{id}" />
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
              <QueryError error={eventsQuery.error} onRetry={() => eventsQuery.refetch()} title="无法加载事件" endpoint="GET /api/v1/evaluations/{id}/events" />
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
        <Form form={form} layout="vertical" initialValues={{ name: "合成评测", case_count: 2 }} onFinish={(values) => createMutation.mutate(values)}>
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

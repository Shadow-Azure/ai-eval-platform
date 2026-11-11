import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Form, Input, InputNumber, Modal, Space, Table, Tag, message } from "antd";
import type { TableColumnsType } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import {
  TERMINAL_EVAL_STATUSES,
  cancelEvaluation,
  createEvaluation,
  fetchEvaluations,
  type EvaluationSummary,
} from "../api/evaluations";

const evaluationsQueries = {
  list: ["evaluations"] as const,
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

interface CreateFormValues {
  name: string;
  case_count: number;
}

export function EvalsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<CreateFormValues>();

  const listQuery = useQuery({
    queryKey: evaluationsQueries.list,
    queryFn: ({ signal }) => fetchEvaluations(signal),
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
      navigate(`/evals/${created.id}`);
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "创建失败"),
  });

  const cancelMutation = useMutation({
    mutationFn: (runId: string) => cancelEvaluation(runId),
    onSuccess: () => {
      message.success("已取消");
      void queryClient.invalidateQueries({ queryKey: evaluationsQueries.list });
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
          <Button type="link" size="small" onClick={() => navigate(`/evals/${row.id}`)}>
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

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, message } from "antd";
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
  fetchEvaluations,
  type EvaluationListQuery,
  type EvaluationSummary,
} from "../api/evaluations";
import { fetchEvalTemplates, type EvalTemplate } from "../api/evalTemplates";
import { TemplateLaunchModal } from "../components/TemplateLaunchModal";
import { TemplateStatusTag, templateLastRunLabel } from "../components/TemplateBits";
import { EVAL_STATUS_OPTIONS, evalStatusMeta } from "../utils/evalStatus";
import {
  dateTimeLabel,
  durationCompact,
  durationLabel,
  progressPercent,
  rateLabel,
  runtimeLabel,
} from "../utils/evaluationDisplay";

const PAGE_SIZE = 10;

const evaluationsQueries = {
  all: ["evaluations"] as const,
  list: (filters: EvaluationListQuery, page: number) => ["evaluations", "list", filters, page] as const,
};

function StatusTag({ status }: { status: string }) {
  const meta = evalStatusMeta(status);
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

interface CreateFormValues {
  name: string;
  case_count: number;
}

export function EvalsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [launchTarget, setLaunchTarget] = useState<EvalTemplate | null>(null);
  const [form] = Form.useForm<CreateFormValues>();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [datasetFilter, setDatasetFilter] = useState("all");
  const [page, setPage] = useState(1);
  const trimmedSearch = search.trim();

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(trimmedSearch);
      setPage(1);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [trimmedSearch]);

  const listQuery = useQuery({
    queryKey: evaluationsQueries.list(
      {
        q: debouncedSearch,
        status: statusFilter,
        datasetId: datasetFilter,
      },
      page,
    ),
    queryFn: ({ signal }) =>
      fetchEvaluations(
        {
          q: debouncedSearch || undefined,
          status: statusFilter === "all" ? undefined : statusFilter,
          datasetId: datasetFilter === "all" ? undefined : datasetFilter,
          limit: PAGE_SIZE,
          offset: (page - 1) * PAGE_SIZE,
        },
        signal,
      ),
    placeholderData: keepPreviousData,
    refetchInterval: 3000,
  });

  const total = listQuery.data?.total;
  useEffect(() => {
    if (total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (page > lastPage) setPage(lastPage);
  }, [page, total]);

  const templatesQuery = useQuery({
    queryKey: ["eval-templates", "quick-launch"],
    queryFn: ({ signal }) => fetchEvalTemplates({ limit: 3 }, signal),
    staleTime: 15_000,
  });

  const datasetsQuery = useQuery({
    queryKey: ["datasets", "eval-filter"],
    queryFn: ({ signal }) => fetchDatasets(signal),
    staleTime: 60_000,
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
      navigate(`/evals/${created.id}`);
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "创建失败"),
  });

  const cancelMutation = useMutation({
    mutationFn: (runId: string) => cancelEvaluation(runId),
    onSuccess: () => {
      message.success("已取消");
      void queryClient.invalidateQueries({ queryKey: evaluationsQueries.all });
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
    setPage(1);
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
        description="一次评测由数据集版本、Agent 运行时、Git 资产源、模型凭据与资源池共同定义。"
        actions={
          <>
            <Button icon={<Icon name="refresh" size={14} />} onClick={() => listQuery.refetch()}>
              刷新
            </Button>
            <Button icon={<Icon name="plus" size={14} />} onClick={() => navigate("/evals/new?mode=template")}>
              新建模板
            </Button>
            <Button type="primary" icon={<Icon name="play" size={14} />} onClick={() => navigate("/evals/new")}>
              发起评测
            </Button>
          </>
        }
      />

      <section className="card quick-template-section section-gap">
        <div className="card-header">
          <div>
            <h2 className="card-title">快速启动</h2>
            <p className="card-subtitle">
              只展示最近使用的 3 个模板；模板数量增长后，完整列表支持搜索、筛选与分页。
            </p>
          </div>
          <div className="card-actions">
            <span className="small muted">共 {templatesQuery.data?.total ?? 0} 个模板</span>
            <Button size="small" onClick={() => navigate("/evals/templates")}>
              全部模板 <Icon name="arrowRight" size={12} />
            </Button>
          </div>
        </div>
        {templatesQuery.data?.items.length ? (
          <div className="quick-template-list">
            {templatesQuery.data.items.map((template) => (
              <div className="quick-template-row" key={template.id}>
                <div className="quick-template-glyph">
                  <Icon name="boxes" size={16} />
                </div>
                <div className="quick-template-main">
                  <Button
                    type="link"
                    size="small"
                    className="template-name-link"
                    onClick={() => navigate(`/evals/templates/${template.id}`)}
                  >
                    {template.name}
                  </Button>
                  <div className="quick-template-combo">
                    {template.dataset_name ?? "未绑定数据集"} · {template.runtime_key} ·{" "}
                    {template.model_display_name ?? template.model_name ?? "模型未配置"} ·{" "}
                    {template.resource_pool_name ?? "资源池未配置"}
                  </div>
                </div>
                <div className="quick-template-last">
                  <span>{templateLastRunLabel(template)}</span>
                  <span>{dateTimeLabel(template.last_launched_at)}</span>
                </div>
                <div className="quick-template-actions">
                  <TemplateStatusTag enabled={template.enabled} />
                  <Button size="small" onClick={() => navigate(`/evals/templates/${template.id}`)}>
                    详情
                  </Button>
                  <Button
                    size="small"
                    type="primary"
                    icon={<Icon name="play" size={12} />}
                    disabled={!template.enabled}
                    onClick={() => setLaunchTarget(template)}
                  >
                    启动
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="card-body">
            <EmptyState
              icon="boxes"
              title="还没有评测模板"
              description="把常用的数据集、Agent 资产、模型与资源池保存为模板，之后一键启动。"
              action={
                <Button
                  size="small"
                  type="primary"
                  onClick={() => navigate("/evals/new?mode=template")}
                >
                  新建模板
                </Button>
              }
            />
          </div>
        )}
      </section>

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
                onChange={(value) => {
                  setStatusFilter(value);
                  setPage(1);
                }}
                style={{ width: 170 }}
              />
              <Select
                aria-label="数据集筛选"
                value={datasetFilter}
                options={datasetOptions}
                onChange={(value) => {
                  setDatasetFilter(value);
                  setPage(1);
                }}
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
              pagination={{
                current: page,
                pageSize: PAGE_SIZE,
                total: total ?? 0,
                showSizeChanger: false,
                hideOnSinglePage: true,
                onChange: (nextPage) => setPage(nextPage),
              }}
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


      <TemplateLaunchModal
        template={launchTarget}
        open={Boolean(launchTarget)}
        onClose={() => setLaunchTarget(null)}
      />

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

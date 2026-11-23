import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Descriptions, Modal, Space, Table, Tag, message } from "antd";
import type { TableColumnsType } from "antd";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../app/auth";
import { ApiError } from "../api/types";
import {
  fetchEvalTemplate,
  updateEvalTemplate,
  type EvalTemplate,
  type EvalTemplateRecentRun,
} from "../api/evalTemplates";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { TemplateApiModal } from "../components/TemplateApiModal";
import {
  TemplateStatusTag,
  caseSelectionLabel,
  costEstimateLabel,
  recentRunLabel,
  retryLabel,
  selfEvolutionLabel,
  selfEvolutionTokenLabel,
} from "../components/TemplateBits";
import { TemplateLaunchModal } from "../components/TemplateLaunchModal";
import { dateTimeLabel, durationLabel, rateLabel } from "../utils/evaluationDisplay";
import { evalStatusMeta } from "../utils/evalStatus";

function TemplateNotFound() {
  const navigate = useNavigate();
  return (
    <div className="page">
      <PageHeading eyebrow="Evaluation · Templates" title="模板不存在" />
      <EmptyState
        icon="search"
        title="没有找到这个评测模板"
        description="模板可能已被删除；返回列表查看仍然可用的模板。"
        action={
          <Button type="primary" onClick={() => navigate("/evals/templates")}>
            返回模板列表
          </Button>
        }
      />
    </div>
  );
}

export function EvalTemplateDetailPage() {
  const { templateId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { session } = useAuth();
  const isAdmin = session?.role === "admin";
  const [launchOpen, setLaunchOpen] = useState(false);
  const [apiOpen, setApiOpen] = useState(false);
  const [disableOpen, setDisableOpen] = useState(false);

  const query = useQuery({
    queryKey: ["eval-templates", templateId],
    queryFn: ({ signal }) => fetchEvalTemplate(templateId, signal),
    enabled: Boolean(templateId),
    retry: false,
  });

  const toggle = useMutation({
    mutationFn: (template: EvalTemplate) =>
      updateEvalTemplate(template.id, { enabled: !template.enabled }, template.version),
    onSuccess: (template) => {
      message.success(template.enabled ? "模板已启用" : "模板已停用");
      setDisableOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["eval-templates"] });
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "更新失败"),
  });

  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return <TemplateNotFound />;
    }
    return (
      <div className="page">
        <PageHeading eyebrow="Evaluation · Templates" title="评测模板" />
        <QueryError
          error={query.error}
          onRetry={() => query.refetch()}
          title="无法加载评测模板"
          endpoint={`GET /api/v1/eval-templates/${templateId}`}
        />
      </div>
    );
  }

  const template = query.data;
  if (!template) {
    return (
      <div className="page">
        <PageHeading eyebrow="Evaluation · Templates" title="评测模板" />
        <EmptyState icon="boxes" title="正在加载模板…" />
      </div>
    );
  }

  const recentColumns: TableColumnsType<EvalTemplateRecentRun> = [
    {
      title: "运行",
      key: "run",
      render: (_value, row) => (
        <div>
          <div className="table-primary mono">{row.id.slice(0, 12)}</div>
          <div className="table-secondary">
            版本 v{row.template_version ?? 1} · {dateTimeLabel(row.created_at)}
          </div>
        </div>
      ),
    },
    {
      title: "状态",
      key: "status",
      render: (_value, row) => {
        const meta = evalStatusMeta(row.status);
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "结果",
      key: "result",
      render: (_value, row) => (
        <div>
          <strong>{rateLabel(row.resolve_rate)}</strong>
          <div className="table-secondary">
            {row.resolved_count ?? 0} / {row.scored_count ?? 0} 已评分
          </div>
        </div>
      ),
    },
    {
      title: "耗时",
      key: "duration",
      render: (_value, row) => durationLabel(row.duration_seconds),
    },
    {
      title: "",
      key: "open",
      render: (_value, row) => (
        <Button size="small" onClick={() => navigate(`/evals/${row.id}`)}>
          打开 <Icon name="arrowRight" size={12} />
        </Button>
      ),
    },
  ];

  return (
    <div className="page">
      <div className="row small muted" style={{ marginBottom: 10 }}>
        <Button
          type="text"
          size="small"
          icon={<Icon name="arrowLeft" size={13} />}
          onClick={() => navigate("/evals/templates")}
        >
          返回模板列表
        </Button>
      </div>
      <PageHeading
        eyebrow={`Template · ${template.id}`}
        title={
          <span className="eval-title-row">
            {template.name} <TemplateStatusTag enabled={template.enabled} />
          </span>
        }
        description={`${template.dataset_name ?? "—"} ${template.dataset_version ?? ""} · ${template.runtime_key} · ${template.model_display_name ?? template.model_name ?? "—"} · ${template.resource_pool_name ?? "—"}`}
        actions={
          <Space wrap>
            <Button icon={<Icon name="pipeline" size={14} />} onClick={() => setApiOpen(true)}>
              接口
            </Button>
            {isAdmin ? (
              template.enabled ? (
                <Button onClick={() => setDisableOpen(true)}>停用</Button>
              ) : (
                <Button loading={toggle.isPending} onClick={() => toggle.mutate(template)}>
                  启用
                </Button>
              )
            ) : null}
            <Button
              type="primary"
              icon={<Icon name="play" size={14} />}
              disabled={!template.enabled || !isAdmin}
              onClick={() => setLaunchOpen(true)}
            >
              一键启动
            </Button>
          </Space>
        }
      />

      <div className="template-detail-layout">
        <div className="stack">
          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">固定配置</h2>
                <p className="card-subtitle">启动时不再询问；引用失效会阻止启动</p>
              </div>
              <Tag color="blue">已固化</Tag>
            </div>
            <div className="card-body">
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="数据集">
                  {template.dataset_name ?? "—"} · {template.dataset_version ?? "—"}
                  <span className="small muted"> · {template.case_count} 个用例</span>
                </Descriptions.Item>
                <Descriptions.Item label="Agent 运行时">
                  {template.runtime_key} {template.runtime_version ?? ""}
                </Descriptions.Item>
                <Descriptions.Item label="Agent 资产">
                  {template.asset_source_name ?? "仅使用 Runtime 内置能力"}
                  <div className="small muted mono">
                    {template.asset_source_name
                      ? `${template.asset_ref ?? "默认 Ref"} · ${template.asset_path_count} 项`
                      : "—"}
                  </div>
                </Descriptions.Item>
                <Descriptions.Item label="模型">
                  {template.model_provider ?? "—"} · {template.model_display_name ?? "—"}
                  <div className="small muted">{template.model_name ?? "—"} · 启动时换发短期凭据</div>
                </Descriptions.Item>
                <Descriptions.Item label="资源池">
                  {template.resource_pool_name ?? "—"}
                  <div className="small muted">
                    {template.resource_pool_max_capacity != null
                      ? `最大容量 ${template.resource_pool_max_capacity}`
                      : "容量以当前在线 Runner 为准"}
                  </div>
                </Descriptions.Item>
                <Descriptions.Item label="自进化上报">
                  {selfEvolutionLabel(template)}
                  <div className="small muted">
                    {selfEvolutionTokenLabel(template)} ·{" "}
                    {template.self_evolution.enabled
                      ? "未通过用例在终态上报；令牌加密保存，不回显"
                      : "未启用：评测行为与现状一致"}
                  </div>
                </Descriptions.Item>
              </Descriptions>
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">执行策略</h2>
                <p className="card-subtitle">与固定配置一起冻结到每次启动的快照</p>
              </div>
            </div>
            <div className="card-body">
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="用例范围">{caseSelectionLabel(template)}</Descriptions.Item>
                <Descriptions.Item label="最大并发">
                  {template.execution.concurrency} Runner
                </Descriptions.Item>
                <Descriptions.Item label="单用例超时">
                  {template.execution.case_timeout_seconds
                    ? durationLabel(template.execution.case_timeout_seconds)
                    : "未固定"}
                </Descriptions.Item>
                <Descriptions.Item label="模型预算">
                  {template.execution.model_budget_cny != null
                    ? `¥${Number(template.execution.model_budget_cny).toFixed(2)}`
                    : "不设置"}
                </Descriptions.Item>
                <Descriptions.Item label="失败重试">{retryLabel(template)}</Descriptions.Item>
                <Descriptions.Item label="最大轮次">
                  {template.max_turns ?? "默认 200"} 轮
                </Descriptions.Item>
                <Descriptions.Item label="Agent 超时">
                  {template.agent_timeout_seconds
                    ? `${Math.round(template.agent_timeout_seconds / 60)} 分钟`
                    : "数据集默认"}
                </Descriptions.Item>
                <Descriptions.Item label="附加指令">
                  <span style={{ whiteSpace: "pre-wrap" }}>
                    {template.prompt_suffix || "未设置"}
                  </span>
                </Descriptions.Item>
                <Descriptions.Item label="费用估算">
                  {costEstimateLabel(template.estimated_cost)}
                </Descriptions.Item>
              </Descriptions>
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">最近运行</h2>
                <p className="card-subtitle">每次启动创建独立评测，模板编辑不回溯修改</p>
              </div>
              <Button
                size="small"
                icon={<Icon name="play" size={13} />}
                disabled={!template.enabled || !isAdmin}
                onClick={() => setLaunchOpen(true)}
              >
                再启动一次
              </Button>
            </div>
            <Table
              rowKey="id"
              size="small"
              pagination={false}
              columns={recentColumns}
              dataSource={template.recent_runs ?? []}
              locale={{
                emptyText: (
                  <EmptyState
                    icon="play"
                    title="还没有从该模板启动过评测"
                    description="一键启动后会在这里显示最近运行、结果与耗时。"
                  />
                ),
              }}
            />
          </section>
        </div>

        <aside className="stack">
          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">启动概览</h2>
                <p className="card-subtitle">模板复用与最近状态</p>
              </div>
            </div>
            <div className="card-body">
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="累计启动">
                  {template.launch_count} 次
                </Descriptions.Item>
                <Descriptions.Item label="最近一次">
                  {dateTimeLabel(template.last_launched_at)}
                  <div className="small muted">{recentRunLabel(template)}</div>
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  <TemplateStatusTag enabled={template.enabled} />
                </Descriptions.Item>
                <Descriptions.Item label="创建人">{template.created_by}</Descriptions.Item>
                <Descriptions.Item label="最近更新">
                  {dateTimeLabel(template.updated_at)}
                </Descriptions.Item>
                <Descriptions.Item label="版本">v{template.version}</Descriptions.Item>
              </Descriptions>
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">接口</h2>
                <p className="card-subtitle">列表查询与三个执行接口</p>
              </div>
              <Tag>3 + 列表</Tag>
            </div>
            <div className="card-body template-api-mini">
              <div>
                <Tag>GET</Tag>
                <code>/eval-templates?q=&amp;status=&amp;limit=&amp;offset=</code>
              </div>
              <div>
                <Tag color="blue">POST</Tag>
                <code>/eval-templates/{template.id.slice(0, 8)}…/launch</code>
              </div>
              <div>
                <Tag>GET</Tag>
                <code>/eval-runs/{"{run_id}"}/status</code>
              </div>
              <div>
                <Tag>GET</Tag>
                <code>/eval-runs/{"{run_id}"}/results</code>
              </div>
              <Button size="small" onClick={() => setApiOpen(true)}>
                查看 curl 示例 <Icon name="arrowRight" size={12} />
              </Button>
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">边界</h2>
                <p className="card-subtitle">一键启动不改变执行链路</p>
              </div>
            </div>
            <div className="card-body small muted">
              启动请求不能覆盖固定字段；模板停用或引用被删除时返回明确错误。每次启动都重新解析资产 Ref
              并冻结新的不可变快照，已启动的评测不受后续编辑影响。
            </div>
          </section>
        </aside>
      </div>

      <TemplateLaunchModal
        template={template}
        open={launchOpen}
        onClose={() => setLaunchOpen(false)}
      />
      <TemplateApiModal
        template={template}
        open={apiOpen}
        onClose={() => setApiOpen(false)}
        onLaunch={() => {
          setApiOpen(false);
          setLaunchOpen(true);
        }}
      />
      <Modal
        title="停用这个模板？"
        open={disableOpen}
        onCancel={() => setDisableOpen(false)}
        onOk={() => toggle.mutate(template)}
        okText="停用"
        cancelText="取消"
        confirmLoading={toggle.isPending}
      >
        <p>
          停用后模板不能一键启动，已启动的评测不受影响。之后仍可在模板列表或详情页重新启用。
        </p>
      </Modal>
    </div>
  );
}

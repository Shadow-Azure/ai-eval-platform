import { Button, Skeleton, Table } from "antd";
import type { TableColumnsType } from "antd";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useOverviewQuery, useReadyzQuery } from "../api/overview";
import { ApiError } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Icon, type IconName } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { StatusCard } from "../components/StatusCard";

interface RecentEvalRow {
  id: string;
  name: string;
  agent: string;
  model: string;
  dataset: string;
  status: string;
  progress: number;
  resolvedRate: string;
  startedAt: string;
}

const statusMeta: Record<string, { label: string; tone: string }> = {
  running: { label: "运行中", tone: "primary" },
  queued: { label: "排队中", tone: "neutral" },
  completed: { label: "已完成", tone: "success" },
  partial: { label: "部分成功", tone: "warning" },
  failed: { label: "失败", tone: "danger" },
};

function EvalStatusTag({ status }: { status: string }) {
  const meta = statusMeta[status] ?? { label: status, tone: "neutral" };
  return (
    <span className={`tag tag-${meta.tone}`}>
      <span className="status-dot" aria-hidden="true" />
      {meta.label}
    </span>
  );
}

const recentEvalColumns: TableColumnsType<RecentEvalRow> = [
  {
    title: "评测任务",
    dataIndex: "name",
    key: "name",
    render: (_value: string, row) => (
      <div>
        <div className="table-primary">{row.name}</div>
        <div className="table-secondary">{row.id}</div>
      </div>
    ),
  },
  {
    title: "Agent / 模型",
    dataIndex: "agent",
    key: "agent",
    render: (_value: string, row) => (
      <div>
        <div>{row.agent}</div>
        <div className="table-secondary">{row.model}</div>
      </div>
    ),
  },
  {
    title: "数据集",
    dataIndex: "dataset",
    key: "dataset",
    render: (value: string) => <div>{value}</div>,
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    render: (value: string) => <EvalStatusTag status={value} />,
  },
  {
    title: "进展",
    dataIndex: "progress",
    key: "progress",
    render: (value: number) => (
      <div className="table-progress">
        <div className="progress">
          <span style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
        </div>
        <span>{value}%</span>
      </div>
    ),
  },
  {
    title: "核心指标",
    dataIndex: "resolvedRate",
    key: "resolvedRate",
    render: (value: string) => (
      <div>
        <strong>{value}</strong>
        <div className="table-secondary">解决率</div>
      </div>
    ),
  },
  {
    title: "开始时间",
    dataIndex: "startedAt",
    key: "startedAt",
    render: (value: string) => <div>{value}</div>,
  },
];

interface HealthRowProps {
  icon: IconName;
  label: string;
  children: ReactNode;
}

function HealthRow({ icon, label, children }: HealthRowProps) {
  return (
    <div className="health-row">
      <span className="health-label">
        <Icon name={icon} size={15} />
        {label}
      </span>
      <span className="health-value">{children}</span>
    </div>
  );
}

function OverviewSkeleton() {
  return (
    <>
      <div className="grid grid-4">
        {[0, 1, 2, 3].map((index) => (
          <StatusCard key={index} label="" value="" icon="overview" loading />
        ))}
      </div>
      <div className="grid dashboard-grid section-gap">
        {[0, 1].map((index) => (
          <section className="card" key={index} aria-busy="true">
            <div className="card-header">
              <div>
                <div className="skeleton skeleton-heading" />
                <div className="skeleton skeleton-subheading" />
              </div>
            </div>
            <div className="card-body">
              <Skeleton active title={false} paragraph={{ rows: 4 }} />
            </div>
          </section>
        ))}
      </div>
      <section className="card section-gap" aria-busy="true">
        <div className="card-header">
          <div>
            <div className="skeleton skeleton-heading" />
            <div className="skeleton skeleton-subheading" />
          </div>
        </div>
        <div className="card-body">
          <Skeleton active title={false} paragraph={{ rows: 3 }} />
        </div>
      </section>
    </>
  );
}

export function OverviewPage() {
  const navigate = useNavigate();
  const overviewQuery = useOverviewQuery();
  const readyzQuery = useReadyzQuery();
  const { data, error, isError, isFetching, isLoading, refetch } = overviewQuery;

  const counts = data?.counts;
  const online = counts?.runners_online ?? 0;
  const total = counts?.runners_total ?? 0;
  const capacityPercent = total > 0 ? Math.min(100, Math.round((online / total) * 100)) : 0;
  const apiHealthy = data?.status === "ok";

  let healthLabel = "检查中";
  let healthTone = "neutral";
  if (data && !apiHealthy) {
    healthLabel = "API 异常";
    healthTone = "warning";
  } else if (readyzQuery.isError) {
    healthLabel = "部分降级";
    healthTone = "warning";
  } else if (readyzQuery.isSuccess) {
    healthLabel = "运行正常";
    healthTone = "success";
  }

  const readyzErrorTitle =
    readyzQuery.error instanceof ApiError ? readyzQuery.error.message : undefined;

  return (
    <div className="page">
      <PageHeading
        eyebrow="Workspace overview"
        title="平台总览"
        description="实时掌握评测容量、任务进展与值得关注的质量变化。"
        actions={
          <>
            <Button onClick={() => navigate("/datasets")} icon={<Icon name="database" size={14} />}>
              导入数据集
            </Button>
            <Button
              type="primary"
              onClick={() => navigate("/evals/new")}
              icon={<Icon name="plus" size={14} />}
            >
              发起评测
            </Button>
          </>
        }
      />

      <div className="banner info overview-note">
        <Icon name="sparkles" size={15} />
        <div>
          <strong>M2 建设中。</strong>
          控制面与数据库底座正在接入，本轮开放登录与总览；其余导航页面将在后续里程碑补齐。
        </div>
      </div>

      {isLoading ? <OverviewSkeleton /> : null}

      {isError && !data ? (
        <QueryError error={error} onRetry={() => void refetch()} isRetrying={isFetching} />
      ) : null}

      {data ? (
        <>
          {isError ? (
            <div className="banner warning overview-note" role="alert">
              <Icon name="alert" size={15} />
              <div className="overview-note-body">
                <span>刷新总览失败，以下为最近一次成功数据。</span>
                <Button type="link" size="small" onClick={() => void refetch()} loading={isFetching}>
                  重试
                </Button>
              </div>
            </div>
          ) : null}

          <div className="grid grid-4">
            <StatusCard
              label="评测任务总数"
              value={counts?.eval_runs ?? 0}
              icon="overview"
              meta={
                <>
                  <span className="trend-up">{counts?.running ?? 0} 运行中</span>
                  <span>{counts?.queued ?? 0} 排队</span>
                </>
              }
            />
            <StatusCard
              label="运行中评测"
              value={counts?.running ?? 0}
              icon="play"
              meta={<span>{counts?.queued ?? 0} 个任务排队中</span>}
            />
            <StatusCard
              label="失败评测"
              value={counts?.failed ?? 0}
              icon="alert"
              tone={counts?.failed ? "danger" : "warning"}
              meta={<span>{counts?.failed ? "需要关注" : "暂无失败任务"}</span>}
            />
            <StatusCard
              label="在线 Runner"
              value={`${online} / ${total}`}
              icon="server"
              tone="success"
              meta={
                <span>
                  {total > 0 ? `${capacityPercent}% 在线容量` : "尚未注册 Runner"}
                </span>
              }
            />
          </div>

          <div className="grid dashboard-grid section-gap">
            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">运行概况</h2>
                  <p className="card-subtitle">状态来自控制面任务汇总与 Runner 心跳</p>
                </div>
                <div className="card-actions">
                  <Button type="text" size="small" onClick={() => navigate("/evals")}>
                    查看评测任务
                    <Icon name="arrowRight" size={13} />
                  </Button>
                </div>
              </div>
              <div className="card-body">
                <div className="overview-stat-row">
                  <span>运行中评测</span>
                  <strong>{counts?.running ?? 0}</strong>
                </div>
                <div className="overview-stat-row">
                  <span>排队中</span>
                  <strong>{counts?.queued ?? 0}</strong>
                </div>
                <div className="overview-stat-row">
                  <span>失败 / 超时</span>
                  <strong className={counts?.failed ? "text-danger" : undefined}>
                    {counts?.failed ?? 0}
                  </strong>
                </div>
                <div className="capacity-block">
                  <div className="row-between">
                    <span className="capacity-label">Runner 在线容量</span>
                    <span className="capacity-value">
                      <strong>{online}</strong> / {total}
                    </span>
                  </div>
                  <div
                    className="progress"
                    role="progressbar"
                    aria-label="Runner 在线容量"
                    aria-valuemin={0}
                    aria-valuemax={Math.max(total, 1)}
                    aria-valuenow={Math.min(online, total)}
                    aria-valuetext={`${online} / ${total} 台在线`}
                  >
                    <span style={{ width: `${capacityPercent}%` }} />
                  </div>
                  <div className="small muted">
                    {total > 0 ? `${capacityPercent}% 在线` : "注册 Runner 后显示在线容量"}
                  </div>
                </div>
              </div>
            </section>

            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">系统健康</h2>
                  <p className="card-subtitle">平台与执行通道</p>
                </div>
                <span className={`tag tag-${healthTone}`}>{healthLabel}</span>
              </div>
              <div className="card-body stack health-list">
                <HealthRow icon="cloud" label="API 服务">
                  <span className={`tag tag-${apiHealthy ? "success" : "warning"}`}>
                    {apiHealthy ? "健康" : data.status || "异常"}
                  </span>
                </HealthRow>
                <HealthRow icon="check" label="就绪检查">
                  <span
                    className={`tag tag-${
                      readyzQuery.isError ? "warning" : readyzQuery.isSuccess ? "success" : "neutral"
                    }`}
                    title={readyzErrorTitle}
                  >
                    {readyzQuery.isError ? "未就绪" : readyzQuery.isSuccess ? "就绪" : "检查中"}
                  </span>
                </HealthRow>
                <HealthRow icon="users" label="默认组织">
                  <span className="health-entity" title={`slug: ${data.tenant.slug}`}>
                    {data.tenant.name}
                  </span>
                  <span className="mono health-slug">{data.tenant.slug}</span>
                </HealthRow>
                <HealthRow icon="database" label="默认项目">
                  <span className="health-entity" title={`slug: ${data.project.slug}`}>
                    {data.project.name}
                  </span>
                  <span className="mono health-slug">{data.project.slug}</span>
                </HealthRow>
              </div>
            </section>
          </div>

          <section className="card section-gap">
            <div className="card-header">
              <div>
                <h2 className="card-title">最近评测</h2>
                <p className="card-subtitle">结果指标由数据集版本定义，不在界面侧写死</p>
              </div>
              <div className="card-actions">
                <span className="tag tag-neutral">M2 建设中</span>
              </div>
            </div>
            <Table<RecentEvalRow>
              className="recent-table"
              columns={recentEvalColumns}
              dataSource={[]}
              rowKey="id"
              pagination={false}
              locale={{
                emptyText: (
                  <EmptyState
                    compact
                    inline
                    icon="list"
                    title="暂无评测任务"
                    description="评测执行链路接入后，运行状态与核心指标将在这里展示。"
                    action={
                      <Button size="small" onClick={() => navigate("/evals/new")}>
                        发起评测
                      </Button>
                    }
                  />
                ),
              }}
            />
          </section>
        </>
      ) : null}
    </div>
  );
}

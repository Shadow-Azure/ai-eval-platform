import { Button, Skeleton, Table } from "antd";
import type { TableColumnsType } from "antd";
import { useOperationsQuery } from "../api/operations";
import type {
  DatabaseTableStatus,
  RunnerStatus,
  ServiceStatusItem,
} from "../api/operations";
import { ApiError } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Icon, type IconName } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { StatusCard } from "../components/StatusCard";

const numberFormat = new Intl.NumberFormat("zh-CN");

const serviceStatusMeta: Record<string, { label: string; tone: string }> = {
  ok: { label: "正常", tone: "success" },
  degraded: { label: "降级", tone: "warning" },
  error: { label: "异常", tone: "danger" },
  disabled: { label: "未启用", tone: "neutral" },
  unknown: { label: "待接入", tone: "neutral" },
};

const runnerStatusMeta: Record<string, { label: string; tone: string }> = {
  online: { label: "在线", tone: "success" },
  busy: { label: "忙碌", tone: "primary" },
  offline: { label: "离线", tone: "neutral" },
  draining: { label: "排空中", tone: "warning" },
};

const poolStatusMeta: Record<string, { label: string; tone: string }> = {
  active: { label: "运行中", tone: "success" },
  disabled: { label: "已停用", tone: "neutral" },
};

const serviceIcons: Record<string, IconName> = {
  api: "cloud",
  database: "database",
  redis: "cpu",
  object_storage: "boxes",
  runner: "server",
  worker: "pulse",
  model_gateway: "key",
};

function statusTag(status: string, labels: Record<string, { label: string; tone: string }>) {
  const meta = labels[status] ?? { label: status || "未知", tone: "neutral" };
  return (
    <span className={`tag tag-${meta.tone}`}>
      <span className="status-dot" aria-hidden="true" />
      {meta.label}
    </span>
  );
}

function formatNumber(value: number | null): string {
  return value === null ? "—" : numberFormat.format(value);
}

function formatBytes(value: number | null): string {
  if (value === null) {
    return "—";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function relativeTime(value: string | null): string {
  if (!value) {
    return "—";
  }
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return "—";
  }
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) {
    return `${seconds} 秒前`;
  }
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)} 分钟前`;
  }
  if (seconds < 86_400) {
    return `${Math.floor(seconds / 3600)} 小时前`;
  }
  return `${Math.floor(seconds / 86_400)} 天前`;
}

function checkedAt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }
  return parsed.toLocaleTimeString("zh-CN", { hour12: false });
}

const runnerColumns: TableColumnsType<RunnerStatus> = [
  {
    title: "Runner",
    dataIndex: "name",
    key: "name",
    render: (_value: string, row) => (
      <div>
        <div className="table-primary">{row.name}</div>
        <div className="table-secondary mono">{row.provider_runner_id}</div>
      </div>
    ),
  },
  {
    title: "资源池",
    dataIndex: "pool",
    key: "pool",
    render: (value: string) => <span>{value}</span>,
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    render: (value: string) => statusTag(value, runnerStatusMeta),
  },
  {
    title: "最近心跳",
    dataIndex: "last_heartbeat_at",
    key: "last_heartbeat_at",
    render: (value: string | null) => (
      <span title={value ?? undefined}>{relativeTime(value)}</span>
    ),
  },
  {
    title: "标签",
    dataIndex: "labels",
    key: "labels",
    render: (value: string[]) => {
      if (value.length === 0) {
        return <span className="muted">—</span>;
      }
      const visible = value.slice(0, 3);
      return (
        <span className="ops-labels">
          {visible.map((label) => (
            <span className="tag tag-neutral" key={label}>
              {label}
            </span>
          ))}
          {value.length > visible.length ? (
            <span className="muted small">+{value.length - visible.length}</span>
          ) : null}
        </span>
      );
    },
  },
];

const databaseColumns: TableColumnsType<DatabaseTableStatus> = [
  {
    title: "数据表",
    dataIndex: "name",
    key: "name",
    render: (_value: string, row) => (
      <div>
        <div className="table-primary mono">{row.name}</div>
        <div className="table-secondary">{row.label}</div>
      </div>
    ),
  },
  {
    title: "行数",
    dataIndex: "rows",
    key: "rows",
    align: "right",
    width: 140,
    render: (value: number | null) => <span className="mono">{formatNumber(value)}</span>,
  },
];

function ServiceRow({ service }: { service: ServiceStatusItem }) {
  const meta = serviceStatusMeta[service.status] ?? {
    label: service.status || "未知",
    tone: "neutral",
  };
  return (
    <div className="ops-service">
      <span className="ops-service-icon" aria-hidden="true">
        <Icon name={serviceIcons[service.key] ?? "pulse"} size={16} />
      </span>
      <div className="ops-service-copy">
        <div className="ops-service-head">
          <span className="ops-service-name">{service.name}</span>
          <span className={`tag tag-${meta.tone}`}>{meta.label}</span>
        </div>
        <div className="ops-service-summary">{service.summary}</div>
      </div>
      {service.latency_ms !== null ? (
        <span className="ops-service-latency mono">{service.latency_ms} ms</span>
      ) : null}
    </div>
  );
}

function StatRow({
  label,
  value,
  meta,
  tone,
}: {
  label: string;
  value: number;
  meta: string;
  tone?: "danger" | "warning";
}) {
  return (
    <div className="ops-stat-row">
      <div>
        <div className="ops-stat-label">{label}</div>
        <div className="ops-stat-meta">{meta}</div>
      </div>
      <strong className={tone ? `ops-stat-value text-${tone}` : "ops-stat-value"}>
        {formatNumber(value)}
      </strong>
    </div>
  );
}

function OperationsSkeleton() {
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
          <Skeleton active title={false} paragraph={{ rows: 5 }} />
        </div>
      </section>
    </>
  );
}

export function OperationsPage() {
  const { data, error, isError, isFetching, isLoading, refetch } = useOperationsQuery();

  if (isLoading) {
    return (
      <div className="page">
        <PageHeading
          eyebrow="Operations · Platform status"
          title="运营状态"
          description="集中查看当前服务、执行资源与数据库状态。仅管理员可见，数据每 30 秒自动刷新。"
        />
        <OperationsSkeleton />
      </div>
    );
  }

  if (isError && !data) {
    return (
      <div className="page">
        <PageHeading
          eyebrow="Operations · Platform status"
          title="运营状态"
          description="集中查看当前服务、执行资源与数据库状态。仅管理员可见，数据每 30 秒自动刷新。"
        />
        <QueryError
          error={error instanceof ApiError ? error : undefined}
          onRetry={() => void refetch()}
          isRetrying={isFetching}
        />
      </div>
    );
  }

  if (!data) {
    return null;
  }

  const services = data.services;
  const runners = data.resources.runners;
  const pools = data.resources.pools;
  const onlineRunners = runners.filter(
    (runner) => runner.status === "online" || runner.status === "busy",
  ).length;
  const healthyServices = services.filter((service) => service.status === "ok").length;
  const attentionServices = services.filter(
    (service) => service.status === "error" || service.status === "degraded",
  ).length;
  const queueDepth =
    data.resources.outbox.pending + data.resources.outbox.processing;
  const tables = data.database.tables;
  const capacity = data.database.capacity;
  const databaseHealthy = data.database.status === "ok";
  const overallHealthy = data.status === "ok";
  const overallLabel = overallHealthy ? "运行正常" : "需要关注";

  return (
    <div className="page">
      <PageHeading
        eyebrow="Operations · Platform status"
        title="运营状态"
        description="集中查看当前服务、执行资源与数据库状态。仅管理员可见，数据每 30 秒自动刷新。"
        actions={
          <>
            <span className="ops-checked-at">
              最近检查 {checkedAt(data.generated_at)} · {data.environment}
            </span>
            <Button
              icon={<Icon name="refresh" size={14} />}
              onClick={() => void refetch()}
              loading={isFetching}
            >
              刷新
            </Button>
          </>
        }
      />

      {isError ? (
        <div className="banner warning ops-note" role="alert">
          <Icon name="alert" size={15} />
          <div className="overview-note-body">
            <span>刷新运营状态失败，以下为最近一次成功数据。</span>
            <Button type="link" size="small" onClick={() => void refetch()} loading={isFetching}>
              重试
            </Button>
          </div>
        </div>
      ) : null}

      {!overallHealthy ? (
        <div className="banner warning ops-note" role="alert">
          <Icon name="alert" size={15} />
          <div>
            <strong>部分服务需要关注。</strong>
            查看下方服务明细定位异常项；未启用项不计入异常。
          </div>
        </div>
      ) : null}

      <div className="grid grid-4">
        <StatusCard
          label="服务状态"
          value={`${healthyServices} / ${services.length}`}
          icon="pulse"
          tone={attentionServices > 0 ? "warning" : "success"}
          meta={
            <span>
              {attentionServices > 0 ? `${attentionServices} 项需要关注` : "已接入服务正常"}
            </span>
          }
        />
        <StatusCard
          label="在线 Runner"
          value={`${onlineRunners} / ${runners.length}`}
          icon="server"
          tone={
            runners.length === 0
              ? "primary"
              : onlineRunners === 0
                ? "warning"
                : "success"
          }
          meta={
            <span>
              {pools.length > 0 ? `${pools.length} 个资源池` : "尚未注册资源池"}
            </span>
          }
        />
        <StatusCard
          label="数据库表"
          value={`${tables.length} 张`}
          icon="database"
          tone={databaseHealthy ? "violet" : "danger"}
          meta={<span>迁移 {data.database.revision ?? "未知"}</span>}
        />
        <StatusCard
          label="调度队列"
          value={formatNumber(queueDepth)}
          icon="cpu"
          tone={
            data.resources.outbox.failed > 0
              ? "danger"
              : queueDepth > 0
                ? "warning"
                : "success"
          }
          meta={
            <span>{data.resources.outbox.failed} 条失败 · 待处理与处理中</span>
          }
        />
      </div>

      <section className="card section-gap">
        <div className="card-header">
          <div>
            <h2 className="card-title">服务状态</h2>
            <p className="card-subtitle">
              控制面、存储、执行与调度依赖的最近一次探活结果
            </p>
          </div>
          <div className="card-actions">
            <span className={`tag tag-${overallHealthy ? "success" : "warning"}`}>
              {overallLabel}
            </span>
          </div>
        </div>
        <div className="card-body">
          <div className="ops-service-grid">
            {services.map((service) => (
              <ServiceRow service={service} key={service.key} />
            ))}
          </div>
        </div>
      </section>

      <div className="grid ops-resource-grid section-gap">
        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">执行资源</h2>
              <p className="card-subtitle">
                {pools.length} 个资源池 · {runners.length} 台 Runner
              </p>
            </div>
          </div>
          {pools.length > 0 ? (
            <div className="ops-pool-list">
              {pools.map((pool) => (
                <div className="ops-pool" key={pool.id}>
                  <div className="ops-pool-head">
                    <span className="ops-pool-name">{pool.name}</span>
                    {statusTag(pool.status, poolStatusMeta)}
                  </div>
                  <div className="ops-pool-meta">
                    {pool.provider} · {pool.runners_online}/{pool.runners_total} 在线
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          <Table<RunnerStatus>
            className="recent-table ops-table"
            columns={runnerColumns}
            dataSource={runners}
            rowKey="id"
            pagination={false}
            locale={{
              emptyText: (
                <EmptyState
                  compact
                  inline
                  icon="server"
                  title="暂无 Runner"
                  description="Runner 上报心跳后会出现在这里。"
                />
              ),
            }}
          />
        </section>

        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">资源清单</h2>
              <p className="card-subtitle">当前租户可见的评测与配置资产</p>
            </div>
          </div>
          <div className="card-body">
            <div className="ops-stat-list">
              <StatRow
                label="评测任务"
                value={data.resources.evaluations.total}
                meta={`运行 ${data.resources.evaluations.running} · 排队 ${data.resources.evaluations.queued}`}
                tone={data.resources.evaluations.failed > 0 ? "danger" : undefined}
              />
              <StatRow
                label="数据集"
                value={data.resources.datasets.total}
                meta={`${data.resources.datasets.versions} 个版本 · ${numberFormat.format(
                  data.resources.datasets.cases,
                )} 条用例`}
              />
              <StatRow
                label="模型配置"
                value={data.resources.model_configs.total}
                meta={`${data.resources.model_configs.usable} 个可用 · ${data.resources.model_configs.credentials} 个凭据`}
              />
              <StatRow
                label="调度队列"
                value={queueDepth}
                meta={`${data.resources.outbox.pending} 待处理 · ${data.resources.outbox.processing} 处理中`}
                tone={data.resources.outbox.failed > 0 ? "warning" : undefined}
              />
            </div>
          </div>
        </section>
      </div>

      <section className="card section-gap">
        <div className="card-header">
          <div>
            <h2 className="card-title">数据库</h2>
            <p className="card-subtitle">
              连通性、迁移版本与关键表行数；只读展示，不包含连接凭据
            </p>
          </div>
          <div className="card-actions">
            {statusTag(data.database.status, {
              ok: { label: "连接正常", tone: "success" },
              error: { label: "连接异常", tone: "danger" },
            })}
          </div>
        </div>
        <div className="card-body">
          <div className="ops-db-meta">
            <div>
              <span>引擎</span>
              <strong>{data.database.dialect ?? "—"}</strong>
            </div>
            <div>
              <span>迁移版本</span>
              <strong className="mono">{data.database.revision ?? "—"}</strong>
            </div>
            <div>
              <span>数据库大小</span>
              <strong>{formatBytes(data.database.size_bytes)}</strong>
            </div>
            <div>
              <span>连接耗时</span>
              <strong>{data.database.latency_ms === null ? "—" : `${data.database.latency_ms} ms`}</strong>
            </div>
          </div>
          <div className="ops-capacity-grid">
            <div className="ops-capacity-card">
              <div className="ops-capacity-head">
                <span>连接数</span>
                <strong>
                  {formatNumber(capacity.connections)}
                  <span className="muted"> / {formatNumber(capacity.max_connections)}</span>
                </strong>
              </div>
              {capacity.connection_usage_percent === null ? (
                <span className="tag tag-neutral">未提供上限</span>
              ) : (
                <div
                  className="progress"
                  role="progressbar"
                  aria-label="数据库连接数水位"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.min(100, Math.max(0, capacity.connection_usage_percent))}
                  aria-valuetext={`${capacity.connection_usage_percent}%`}
                >
                  <span
                    style={{
                      width: `${Math.min(100, Math.max(0, capacity.connection_usage_percent))}%`,
                    }}
                  />
                </div>
              )}
              <div className="ops-capacity-meta">
                {capacity.connection_usage_percent === null
                  ? "当前引擎不提供 max_connections"
                  : `${capacity.connection_usage_percent}% 已使用`}
              </div>
            </div>
            <div className="ops-capacity-card">
              <div className="ops-capacity-head">
                <span>存储容量</span>
                <strong>
                  {formatBytes(data.database.size_bytes)}
                  <span className="muted">
                    {" "}
                    / {formatBytes(capacity.storage_limit_bytes)}
                  </span>
                </strong>
              </div>
              {capacity.storage_usage_percent === null ? (
                <span className="tag tag-neutral">未配置上限</span>
              ) : (
                <div
                  className="progress"
                  role="progressbar"
                  aria-label="数据库存储容量水位"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.min(100, Math.max(0, capacity.storage_usage_percent))}
                  aria-valuetext={`${capacity.storage_usage_percent}%`}
                >
                  <span
                    style={{
                      width: `${Math.min(100, Math.max(0, capacity.storage_usage_percent))}%`,
                    }}
                  />
                </div>
              )}
              <div className="ops-capacity-meta">
                {capacity.storage_usage_percent === null
                  ? "设置 DATABASE_STORAGE_LIMIT_BYTES 后显示水位"
                  : `${capacity.storage_usage_percent}% 已使用`}
              </div>
            </div>
          </div>
          <Table<DatabaseTableStatus>
            className="recent-table ops-table ops-db-table"
            columns={databaseColumns}
            dataSource={tables}
            rowKey="name"
            pagination={false}
            locale={{
              emptyText: (
                <EmptyState
                  compact
                  inline
                  icon="database"
                  title="暂无数据库表信息"
                  description="数据库不可用时不会返回表行数。"
                />
              ),
            }}
          />
        </div>
      </section>

      <div className="ops-footer-note">
        <Icon name="lock" size={13} />
        <span>运营状态为只读视图，仅平台管理员可见。</span>
      </div>
    </div>
  );
}

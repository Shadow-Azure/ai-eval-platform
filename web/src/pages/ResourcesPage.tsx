import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Skeleton,
  Space,
  Table,
  Tabs,
  Timeline,
  message,
} from "antd";
import type { TableColumnsType } from "antd";
import { useAuth } from "../app/auth";
import { ApiError } from "../api/types";
import {
  createResourceConnection,
  createResourceCredential,
  createResourcePool,
  fetchPoolRunners,
  fetchResourceConnections,
  fetchResourceCredentials,
  fetchResourcePool,
  fetchResourcePools,
  labelList,
  testProvisioning,
  updateResourcePool,
  verifyResourceConnection,
  type CapacityState,
  type PoolRunner,
  type ProvisioningStep,
  type ProvisioningTestResult,
  type ResourceConnection,
  type ResourceCredential,
  type ResourcePool,
  type ResourcePoolDetail,
  type RunnerSummary,
  type PoolWriteInput,
  type ProvisioningMode,
} from "../api/resources";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { StatusCard } from "../components/StatusCard";
import { formatMoney, formatUnitPrice } from "../utils/money";

type TabKey = "pools" | "connections";

const numberFormat = new Intl.NumberFormat("zh-CN");
const SUPPORTED_SPOT_PROVIDERS = new Set(["aliyun"]);
const SPOT_CAPABILITY = "ecs:spot";
const INVENTORY_CAPABILITY = "ecs:inventory";

const providerLabels: Record<string, string> = {
  aliyun: "阿里云 ECS",
  github: "GitHub Actions",
  fixed: "自有机器",
  fake: "本地演示供给器",
};

const modeLabels: Record<string, string> = {
  fixed: "自有机器注册",
  spot: "竞价实例 ECS",
};

const connectionStatusMeta: Record<string, { label: string; tone: string }> = {
  verified: { label: "验证通过", tone: "success" },
  unverified: { label: "待验证", tone: "warning" },
  failed: { label: "验证失败", tone: "danger" },
};

const runnerStatusMeta: Record<string, { label: string; tone: string }> = {
  online: { label: "在线", tone: "success" },
  busy: { label: "忙碌", tone: "primary" },
  offline: { label: "离线", tone: "neutral" },
  draining: { label: "排空中", tone: "warning" },
};

const eventTypeLabels: Record<string, string> = {
  "resource.capacity.updated": "容量状态更新",
  "resource.capacity.scaled_down": "缩容释放",
  "resource.capacity.released": "实例已释放",
  "resource.provisioning_failed": "扩容失败",
  "resource.release_failed": "释放失败",
  "resource.provisioning_tested": "供给测试完成",
  "resource.instance.registered": "Runner 已注册",
  "resource.instance.interrupted": "竞价实例被回收",
  "resource.instance.create_timeout": "实例创建超时",
  "resource.provider.sync_failed": "云厂商同步失败",
};

const capacityReasonMeta: Record<
  string,
  { tone: "warning" | "danger" | "info"; title: string; describe: (state: CapacityState) => string }
> = {
  resource_price_above_threshold: {
    tone: "warning",
    title: "当前竞价价格高于阈值",
    describe: (state) =>
      `当前价格 ${formatUnitPrice(state.price_cny)}，阈值 ${formatUnitPrice(
        state.price_threshold_cny,
      )}，平台暂不扩容，${state.queued_attempts} 个排队 Job 会保留并周期性重新询价。`,
  },
  resource_inventory_insufficient: {
    tone: "warning",
    title: "区域或规格暂无库存",
    describe: (state) =>
      `供给器没有可用库存，排队 Job 保持等待；平台会按退避节奏重试，不会把用例判为失败。当前缺口 ${state.capacity_gap}。`,
  },
  resource_provisioning_failed: {
    tone: "danger",
    title: "上次扩容失败",
    describe: () => "扩容失败已写入资源事件，可在详情抽屉查看稳定错误码与诊断摘要。",
  },
  resource_provider_auth_failed: {
    tone: "danger",
    title: "云账号认证失败",
    describe: () => "资源连接的云账号凭据不可用，请到资源连接页执行只读验证并更新凭据。",
  },
  resource_account_balance_insufficient: {
    tone: "danger",
    title: "云账号余额不足",
    describe: () => "余额不足时供给器停止扩容；充值后平台会在下一次检查自动恢复。",
  },
  runner_registration_unavailable: {
    tone: "danger",
    title: "Runner 注册令牌不可用",
    describe: () => "实例创建已停止，避免产生无法接单的计费资源；请检查 Runner 注册配置。",
  },
  provisioner_unavailable: {
    tone: "warning",
    title: "连接没有可用供给器",
    describe: () => "请更换受支持的资源连接，或等待对应供应商的供给器上线。",
  },
  capacity_scale_up: {
    tone: "info",
    title: "正在按排队需求扩容",
    describe: (state) =>
      `当前缺口 ${state.capacity_gap}，正在创建 ${state.starting_instances} 个实例，目标容量 ${state.desired_capacity}。`,
  },
  capacity_scale_down: {
    tone: "info",
    title: "正在缩容空闲容量",
    describe: () => "空闲实例会先排空 Runner 再释放，避免带走正在执行的任务。",
  },
};

const stepLabels: Record<string, string> = {
  cost_confirmed: "费用确认",
  quote: "询价与库存",
  instance_created: "创建实例",
  runner_registration: "注册 Runner",
  instance_released: "自动释放",
};

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function relativeTime(value: string | null | undefined): string {
  if (!value) return "尚无心跳";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "尚无心跳";
  const seconds = Math.max(0, Math.round((Date.now() - parsed.getTime()) / 1000));
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.round(hours / 24)} 天前`;
}

function tagBadge(meta: { label: string; tone: string }) {
  return (
    <span className={`tag tag-${meta.tone}`}>
      <span className="status-dot" aria-hidden="true" />
      {meta.label}
    </span>
  );
}

function statusTag(status: string, labels: Record<string, { label: string; tone: string }>) {
  const meta = labels[status] ?? { label: status || "未知", tone: "neutral" };
  return tagBadge(meta);
}

/**
 * A pool is "ready" when it has capacity to schedule work and "auto-scaling"
 * while the capacity controller is creating instances. This is derived from the
 * capacity snapshot, not from the generic lifecycle status, so the tag matches
 * what the operator sees in the events.
 */
function poolStatusMeta(pool: ResourcePool): { label: string; tone: string } {
  if (pool.status === "disabled") return { label: "已停用", tone: "neutral" };
  const state = pool.capacity_state;
  if (state.starting_instances > 0 || state.capacity_reason === "capacity_scale_up") {
    return { label: "自动扩容中", tone: "primary" };
  }
  return { label: "就绪", tone: "success" };
}

function providerLabel(provider: string): string {
  return providerLabels[provider] ?? provider;
}

function modeLabel(mode: string): string {
  return modeLabels[mode] ?? mode;
}

function liveRunners(summary: RunnerSummary | undefined): number {
  return summary ? summary.online + summary.busy : 0;
}

function connectionOf(
  connections: ResourceConnection[],
  connectionId: string | null,
): ResourceConnection | undefined {
  return connections.find((connection) => connection.id === connectionId);
}

function parseLabels(value: string | undefined): Record<string, unknown> {
  const labels: Record<string, unknown> = {};
  (value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .forEach((item) => {
      const [key, ...rest] = item.split("=");
      if (!key) return;
      const trimmed = key.trim();
      if (!trimmed) return;
      labels[trimmed] = rest.length > 0 ? rest.join("=").trim() : true;
    });
  return labels;
}

function spotCompatibility(
  connections: ResourceConnection[],
  connectionId: string | undefined,
): { connection: ResourceConnection | undefined; reason: string | null } {
  const connection = connectionId
    ? connections.find((item) => item.id === connectionId)
    : undefined;
  if (!connectionId) {
    return { connection, reason: "竞价实例需要先选择资源连接" };
  }
  if (!connection) {
    return { connection, reason: "找不到所选资源连接" };
  }
  if (!SUPPORTED_SPOT_PROVIDERS.has(connection.provider)) {
    return {
      connection,
      reason: `当前没有可用于 ${providerLabel(connection.provider)} 的供给器`,
    };
  }
  if (!connection.capabilities.includes(SPOT_CAPABILITY)) {
    return {
      connection,
      reason: "该连接未声明 ecs:spot 能力，不能创建竞价实例",
    };
  }
  return { connection, reason: null };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function ResourcesPage() {
  const { session } = useAuth();
  const queryClient = useQueryClient();
  const isAdmin = session?.role === "admin";
  const [tab, setTab] = useState<TabKey>("pools");
  const [connectionModalOpen, setConnectionModalOpen] = useState(false);
  const [poolModal, setPoolModal] = useState<{
    pool?: ResourcePool;
    connectionId?: string;
  } | null>(null);
  const [detailPoolId, setDetailPoolId] = useState<string | null>(null);
  const [testPoolId, setTestPoolId] = useState<string | null>(null);

  const poolsQuery = useQuery({
    queryKey: ["resource-pools"],
    queryFn: ({ signal }) => fetchResourcePools(signal),
  });
  const connectionsQuery = useQuery({
    queryKey: ["resource-connections"],
    queryFn: ({ signal }) => fetchResourceConnections(signal),
  });
  const credentialsQuery = useQuery({
    queryKey: ["resource-credentials"],
    queryFn: ({ signal }) => fetchResourceCredentials(signal),
    enabled: isAdmin && connectionModalOpen,
  });

  const pools = poolsQuery.data ?? [];
  const connections = connectionsQuery.data ?? [];
  const detailPool = pools.find((pool) => pool.id === detailPoolId);
  const testPool = pools.find((pool) => pool.id === testPoolId);

  const summary = useMemo(() => {
    const totals = pools.reduce(
      (total, pool) => {
        total.live += liveRunners(pool.runners);
        total.busy += pool.runners.busy;
        total.offline += pool.runners.offline;
        total.capacity += pool.max_capacity;
        total.starting += pool.capacity_state.starting_instances;
        total.costToday += Number(pool.usage.cost.today_cny) || 0;
        total.costSpot += Number(pool.usage.cost.spot_cny) || 0;
        total.unpricedInstances += pool.usage.cost.unpriced_instance_count;
        return total;
      },
      {
        live: 0,
        busy: 0,
        offline: 0,
        capacity: 0,
        starting: 0,
        costToday: 0,
        costSpot: 0,
        unpricedInstances: 0,
      },
    );
    return {
      ...totals,
      utilization: totals.capacity > 0 ? totals.busy / totals.capacity : 0,
    };
  }, [pools]);

  const attentionPools = pools.filter(
    (pool) =>
      pool.capacity_state.capacity_reason === "resource_price_above_threshold" ||
      pool.capacity_state.capacity_reason === "resource_inventory_insufficient" ||
      pool.capacity_state.capacity_reason === "resource_provider_auth_failed" ||
      pool.capacity_state.capacity_reason === "resource_account_balance_insufficient" ||
      pool.usage.queue_risk.at_risk,
  ).length;

  const refreshAll = () => {
    void poolsQuery.refetch();
    void connectionsQuery.refetch();
  };

  const loading = poolsQuery.isLoading || connectionsQuery.isLoading;

  function renderPools() {
    if (poolsQuery.isError && !poolsQuery.data) {
      return (
        <QueryError
          error={poolsQuery.error}
          onRetry={() => void poolsQuery.refetch()}
          isRetrying={poolsQuery.isFetching}
          title="无法加载资源池"
          endpoint="GET /api/v1/resource-pools"
        />
      );
    }
    return (
      <>
        {attentionPools > 0 ? (
          <div className="banner warning resource-note" role="status">
            <Icon name="alert" size={15} />
            <div>
              <strong>{attentionPools} 个资源池需要关注。</strong>
              卡片下方的排队原因会说明是价格、库存、认证还是容量导致等待；在线 Runner
              接近池上限且存在排队 Job 时会显示排队风险，排队用例不会被判失败。
            </div>
          </div>
        ) : null}
        {pools.length === 0 ? (
          <EmptyState
            icon="server"
            title="还没有资源池"
            description="先登记资源连接，再选择兼容的供给模式创建资源池；自有机器可以不使用连接。"
            action={
              isAdmin ? (
                <Space>
                  <Button onClick={() => setConnectionModalOpen(true)}>新建资源连接</Button>
                  <Button type="primary" onClick={() => setPoolModal({})}>
                    新建资源池
                  </Button>
                </Space>
              ) : null
            }
          />
        ) : (
          <div className="pool-grid">
            {pools.map((pool) => {
              const connection = connectionOf(connections, pool.connection_id);
              const state = pool.capacity_state;
              const reason = state.capacity_reason
                ? capacityReasonMeta[state.capacity_reason]
                : undefined;
              return (
                <article className="card pool-card" key={pool.id}>
                  <div className="pool-head">
                    <div
                      className={`pool-icon${pool.provisioning_mode === "spot" ? " spot" : ""}`}
                    >
                      <Icon
                        name={pool.provisioning_mode === "spot" ? "cloud" : "server"}
                        size={18}
                      />
                    </div>
                    <div className="pool-title">
                      <div className="pool-name">{pool.name}</div>
                      <div className="small muted">
                        {connection ? connection.name : "未绑定资源连接"} ·{" "}
                        {modeLabel(pool.provisioning_mode)} ·{" "}
                        {providerLabel(connection?.provider ?? pool.provider_key)}
                      </div>
                    </div>
                    {tagBadge(poolStatusMeta(pool))}
                  </div>

                  <div className="row-between small">
                    <span className="muted">运行容量</span>
                    <strong>
                      {liveRunners(pool.runners)} / {pool.max_capacity} Runner
                    </strong>
                  </div>

                  <PoolCapacityBar pool={pool} />

                  {pool.usage.queue_risk.at_risk ? (
                    <div className="banner warning resource-reason" role="status">
                      <Icon name="alert" size={15} />
                      <div>
                        <strong>排队风险。</strong>
                        排队 {numberFormat.format(pool.usage.queue_risk.queued_attempts)} 个
                        Job（预警阈值 {pool.usage.queue_risk.threshold}），在线{" "}
                        {pool.usage.queue_risk.live_runners} / {pool.max_capacity} Runner
                        已接近或达到池上限；扩容完成后会自动继续调度，排队用例不会被判失败。
                      </div>
                    </div>
                  ) : null}

                  <div className="pool-stats">
                    <PoolStat label="在线" value={liveRunners(pool.runners)} tone="online" />
                    <PoolStat label="忙碌" value={pool.runners.busy} tone="busy" />
                    <PoolStat label="排队" value={state.queued_attempts} tone="queued" />
                    <PoolStat
                      label="离线"
                      value={pool.runners.offline + pool.runners.draining}
                      tone="offline"
                    />
                    <PoolStat label="扩容中" value={state.starting_instances} tone="starting" />
                  </div>

                  <div className="pool-meta">
                    <PoolMeta label="容量区间" value={`${pool.min_capacity} – ${pool.max_capacity}`} />
                    <PoolMeta
                      label="今日资源成本"
                      value={formatMoney(pool.usage.cost.today_cny)}
                    />
                    <PoolMeta
                      label="当前价格 / 阈值（CNY/小时）"
                      value={`${formatUnitPrice(state.price_cny)} / ${formatUnitPrice(
                        state.price_threshold_cny ?? pool.price_threshold_cny,
                      )}`}
                    />
                    <PoolMeta label="当前缺口" value={numberFormat.format(state.capacity_gap)} />
                    <PoolMeta
                      label="最近心跳"
                      value={relativeTime(pool.runners.last_heartbeat_at)}
                    />
                    <PoolMeta
                      label="下次检查"
                      value={formatDateTime(state.next_check_at ?? pool.next_check_at)}
                    />
                  </div>

                  {reason && state.capacity_reason ? (
                    <div
                      className={`banner ${reason.tone === "danger" ? "danger" : reason.tone === "warning" ? "warning" : "success"} resource-reason`}
                      role="status"
                    >
                      <Icon name={reason.tone === "info" ? "pulse" : "alert"} size={15} />
                      <div>
                        <strong>{reason.title}</strong> {reason.describe(state)}
                      </div>
                    </div>
                  ) : null}

                  <div className="row-between pool-footer">
                    <div className="capability-tags">
                      {labelList(pool.labels).slice(0, 4).map((label) => (
                        <span className="tag tag-neutral mono" key={label}>
                          {label}
                        </span>
                      ))}
                      {labelList(pool.labels).length === 0 ? (
                        <span className="small muted">未设置 Runner 标签</span>
                      ) : null}
                    </div>
                    <Space size={8}>
                      <Button size="small" onClick={() => setDetailPoolId(pool.id)}>
                        详情
                      </Button>
                      {isAdmin ? (
                        <Button size="small" onClick={() => setPoolModal({ pool })}>
                          编辑
                        </Button>
                      ) : null}
                    </Space>
                  </div>
                </article>
              );
            })}
            {isAdmin ? (
              <button
                type="button"
                className="card pool-card pool-card-add"
                onClick={() => setPoolModal({})}
              >
                <span className="empty-mark">
                  <Icon name="plus" size={20} />
                </span>
                <strong>接入新的执行资源</strong>
                <span className="small muted">自有机器、ECS 或竞价实例</span>
                <span className="btn-like">新建资源池</span>
              </button>
            ) : null}
          </div>
        )}
      </>
    );
  }

  function renderConnections() {
    if (connectionsQuery.isError && !connectionsQuery.data) {
      return (
        <QueryError
          error={connectionsQuery.error}
          onRetry={() => void connectionsQuery.refetch()}
          isRetrying={connectionsQuery.isFetching}
          title="无法加载资源连接"
          endpoint="GET /api/v1/connections"
        />
      );
    }
    return (
      <>
        {!isAdmin ? (
          <div className="banner resource-note" role="status">
            <Icon name="lock" size={15} />
            <div>
              当前角色可以引用资源连接创建资源池，但不能验证连接或替换云账号密钥。页面只显示脱敏凭据。
            </div>
          </div>
        ) : null}
        {connections.length === 0 ? (
          <EmptyState
            icon="key"
            title="还没有资源连接"
            description="资源连接是租户级接入凭证，可被多个资源池复用。阿里云竞价池需要具备 ecs:spot 能力的连接。"
            action={
              isAdmin ? (
                <Button type="primary" onClick={() => setConnectionModalOpen(true)}>
                  新建资源连接
                </Button>
              ) : null
            }
          />
        ) : (
          <div className="connection-grid">
            {connections.map((connection) => (
              <ConnectionCard
                key={connection.id}
                connection={connection}
                isAdmin={isAdmin}
                onCreatePool={() =>
                  setPoolModal({ connectionId: connection.id })
                }
              />
            ))}
          </div>
        )}
      </>
    );
  }

  return (
    <section className="page">
      <PageHeading
        eyebrow="Configuration · Resource pools"
        title="资源池"
        description="复用自托管 Runner：一个 Runner 同时执行一个 Job，平台并发度等于在线 Runner 数。先登记资源连接，再创建兼容的资源池。"
        actions={
          <>
            <Button
              icon={<Icon name="refresh" size={14} />}
              onClick={refreshAll}
              loading={poolsQuery.isFetching || connectionsQuery.isFetching}
            >
              刷新
            </Button>
            {isAdmin ? (
              <>
                <Button onClick={() => setConnectionModalOpen(true)}>新建资源连接</Button>
                <Button
                  type="primary"
                  icon={<Icon name="plus" size={14} />}
                  onClick={() => setPoolModal({})}
                >
                  新建资源池
                </Button>
              </>
            ) : null}
          </>
        }
      />

      <div className="grid grid-4">
        <StatusCard
          label="在线 Runner"
          value={`${summary.live} / ${summary.capacity}`}
          meta={
            <span>
              {summary.starting > 0
                ? `${summary.starting} 台正在扩容`
                : `${summary.busy} 台忙碌 · ${summary.offline} 台离线`}
            </span>
          }
          icon="server"
          tone={summary.live > 0 ? "success" : "primary"}
          loading={loading && pools.length === 0}
        />
        <StatusCard
          label="最大并发"
          value={numberFormat.format(summary.capacity)}
          meta={<span>{pools.length} 个资源池 · 单 Runner 单 Job</span>}
          icon="cpu"
          loading={loading && pools.length === 0}
        />
        <StatusCard
          label="当前利用率"
          value={`${Math.round(summary.utilization * 100)}%`}
          meta={<span>{summary.busy} 个 Job 执行中</span>}
          icon="play"
          tone="violet"
          loading={loading && pools.length === 0}
        />
        <StatusCard
          label="今日资源成本"
          value={formatMoney(summary.costToday)}
          meta={
            <span>
              {summary.unpricedInstances > 0
                ? `${summary.unpricedInstances} 台实例缺少价格快照`
                : summary.costSpot > 0
                  ? `竞价实例 ${formatMoney(summary.costSpot)}`
                  : "暂无云实例计费"}
            </span>
          }
          icon="cloud"
          tone="warning"
          loading={loading && pools.length === 0}
        />
      </div>

      <section className="card section-gap resources-panel">
        <Tabs
          activeKey={tab}
          onChange={(key) => setTab(key as TabKey)}
          tabBarExtraContent={
            <span className="small muted">心跳与检查时间来自最近一次查询</span>
          }
          items={[
            {
              key: "pools",
              label: "资源池",
              children: renderPools(),
            },
            {
              key: "connections",
              label: "资源连接",
              children: renderConnections(),
            },
          ]}
        />
      </section>

      <ConnectionModal
        open={connectionModalOpen}
        credentials={credentialsQuery.data ?? []}
        credentialsError={credentialsQuery.error}
        onClose={() => setConnectionModalOpen(false)}
        onCreated={() => {
          void connectionsQuery.refetch();
        }}
      />

      {poolModal ? (
        <PoolModal
          key={poolModal.pool?.id ?? poolModal.connectionId ?? "new"}
          open
          pool={poolModal.pool}
          connections={connections}
          initialConnectionId={poolModal.connectionId}
          onClose={() => setPoolModal(null)}
          onSaved={() => {
            void poolsQuery.refetch();
            void connectionsQuery.refetch();
          }}
        />
      ) : null}

      <PoolDetailDrawer
        open={Boolean(detailPoolId)}
        poolId={detailPoolId}
        connections={connections}
        isAdmin={isAdmin}
        onClose={() => setDetailPoolId(null)}
        onEdit={(pool) => {
          setDetailPoolId(null);
          setPoolModal({ pool });
        }}
        onTest={(poolId) => setTestPoolId(poolId)}
      />

      <ProvisioningTestModal
        open={Boolean(testPoolId)}
        pool={testPool ?? detailPool ?? null}
        onClose={() => setTestPoolId(null)}
        onFinished={() => {
          void poolsQuery.refetch();
          if (testPoolId) {
            void queryClient.invalidateQueries({
              queryKey: ["resource-pool", testPoolId],
            });
            void queryClient.invalidateQueries({
              queryKey: ["resource-pool", testPoolId, "runners"],
            });
          }
        }}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Small presentational pieces
// ---------------------------------------------------------------------------

function PoolStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone?: "online" | "busy" | "queued" | "offline" | "starting";
}) {
  return (
    <div className="pool-stat">
      <div className="pool-stat-value">{value}</div>
      <div className="pool-stat-label">
        {tone ? <span className={`capacity-dot ${tone}`} aria-hidden="true" /> : null}
        {label}
      </div>
    </div>
  );
}

function PoolMeta({ label, value }: { label: string; value: string }) {
  return (
    <div className="pool-meta-item">
      <span className="muted small">{label}</span>
      <span className="pool-meta-value">{value}</span>
    </div>
  );
}

/**
 * Colored segmented capacity bar (M5-19).
 *
 * Each segment is a runner/job state the operator can act on: online (idle),
 * busy (executing), queued (waiting job), offline (including draining), and
 * starting (capacity controller creating instances). Empty capacity is the
 * track. The stat row below the bar repeats the same counts with matching
 * colored dots, and the aria label spells them out for screen readers.
 */
function PoolCapacityBar({ pool }: { pool: ResourcePool }) {
  const { runners, capacity_state: state } = pool;
  const online = runners.online;
  const busy = runners.busy;
  const offline = runners.offline + runners.draining;
  const starting = state.starting_instances;
  const queued = state.queued_attempts;
  const occupied = online + busy + offline + starting;
  const empty = Math.max(pool.max_capacity - occupied, 0);
  const segments = [
    { key: "busy", label: "忙碌", count: busy, tone: "busy" },
    { key: "online", label: "在线", count: online, tone: "online" },
    { key: "queued", label: "排队", count: queued, tone: "queued" },
    { key: "offline", label: "离线", count: offline, tone: "offline" },
    { key: "starting", label: "扩容中", count: starting, tone: "starting" },
    { key: "empty", label: "空闲容量", count: empty, tone: "empty" },
  ];
  const description = segments
    .filter((segment) => segment.count > 0)
    .map((segment) => `${segment.label} ${segment.count}`)
    .join("，");
  return (
    <div
      className="capacity-bar"
      role="img"
      aria-label={`资源池容量构成：${description || "暂无数据"}`}
    >
      {segments
        .filter((segment) => segment.count > 0)
        .map((segment) => (
          <span
            key={segment.key}
            className={`capacity-seg ${segment.tone}`}
            style={{ flexGrow: segment.count }}
            title={`${segment.label} ${segment.count}`}
          />
        ))}
    </div>
  );
}

function ConnectionCard({
  connection,
  isAdmin,
  onCreatePool,
}: {
  connection: ResourceConnection;
  isAdmin: boolean;
  onCreatePool: () => void;
}) {
  const queryClient = useQueryClient();
  const [verifying, setVerifying] = useState(false);

  async function verify() {
    setVerifying(true);
    try {
      const updated = await verifyResourceConnection(connection.id);
      if (updated.status === "verified") {
        message.success("只读验证通过");
      } else {
        message.warning("只读验证未通过，请查看连接状态与最近验证时间");
      }
      await queryClient.invalidateQueries({ queryKey: ["resource-connections"] });
    } catch (error) {
      const detail = error instanceof ApiError ? `（${error.code ?? error.status}）` : "";
      message.error(`${error instanceof Error ? error.message : "验证失败"}${detail}`);
      await queryClient.invalidateQueries({ queryKey: ["resource-connections"] });
    } finally {
      setVerifying(false);
    }
  }

  return (
    <article className="card connection-card">
      <div className="pool-head">
        <div className="pool-icon">
          <Icon name="key" size={18} />
        </div>
        <div className="pool-title">
          <div className="pool-name">{connection.name}</div>
          <div className="small muted">
            {providerLabel(connection.provider)} · {connection.region ?? "全局"}
          </div>
        </div>
        {statusTag(connection.status, connectionStatusMeta)}
      </div>

      <div className="connection-facts">
        <div>
          <span className="muted small">能力清单</span>
          <div className="capability-tags">
            {connection.capabilities.length === 0 ? (
              <span className="small muted">未声明能力</span>
            ) : (
              connection.capabilities.map((capability) => (
                <span className="tag tag-primary mono" key={capability}>
                  {capability}
                </span>
              ))
            )}
          </div>
        </div>
        <div className="connection-fact-row">
          <span className="muted small">最近验证</span>
          <span>{formatDateTime(connection.last_verified_at)}</span>
        </div>
        <div className="connection-fact-row">
          <span className="muted small">接入凭据</span>
          <span className="mono">{connection.credential_mask ?? "—（仅显示掩码）"}</span>
        </div>
      </div>

      {connection.status === "failed" ? (
        <div className="banner danger resource-reason" role="status">
          <Icon name="alert" size={15} />
          <div>最近一次只读验证失败。若该连接被竞价池引用，扩容会带上认证失败原因。</div>
        </div>
      ) : null}

      <div className="pool-footer row-between">
        <span className="small muted">
          {SUPPORTED_SPOT_PROVIDERS.has(connection.provider) &&
          connection.capabilities.includes(SPOT_CAPABILITY)
            ? "可用于竞价资源池"
            : "不可用于竞价资源池"}
        </span>
        <Space size={8}>
          {isAdmin ? (
            <Button size="small" onClick={() => void verify()} loading={verifying}>
              只读验证
            </Button>
          ) : null}
          {isAdmin ? (
            <Button size="small" type="primary" onClick={onCreatePool}>
              新建资源池
            </Button>
          ) : null}
        </Space>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Connection create modal
// ---------------------------------------------------------------------------

interface ConnectionFormValues {
  name: string;
  provider: string;
  region?: string;
  capabilities: string[];
  credential_id?: string;
  access_key_id?: string;
  access_key_secret?: string;
}

function ConnectionModal({
  open,
  credentials,
  credentialsError,
  onClose,
  onCreated,
}: {
  open: boolean;
  credentials: ResourceCredential[];
  credentialsError: unknown;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form] = Form.useForm<ConnectionFormValues>();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"existing" | "new">("new");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const provider = Form.useWatch("provider", form) ?? "aliyun";

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    setMode("new");
    setError(null);
    form.setFieldsValue({
      provider: "aliyun",
      region: "cn-hangzhou",
      capabilities: [SPOT_CAPABILITY, INVENTORY_CAPABILITY],
    });
  }, [open, form]);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      capabilities: [SPOT_CAPABILITY, INVENTORY_CAPABILITY],
      region: provider === "aliyun" ? "cn-hangzhou" : undefined,
    });
  }, [form, open, provider]);

  async function submit() {
    let createdCredentialId: string | null = null;
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      setError(null);
      let credentialId = values.credential_id;
      if (mode === "new") {
        const secret =
          values.provider === "aliyun"
            ? JSON.stringify({
                access_key_id: values.access_key_id ?? "",
                access_key_secret: values.access_key_secret ?? "",
              })
            : (values.access_key_secret ?? "");
        const credential = await createResourceCredential({
          provider: values.provider,
          secret,
        });
        createdCredentialId = credential.id;
        credentialId = credential.id;
        void queryClient.invalidateQueries({ queryKey: ["resource-credentials"] });
      }
      if (!credentialId) {
        throw new ApiError("请选择或新建资源凭据", { code: "credential_required" });
      }
      const connection = await createResourceConnection({
        name: values.name,
        provider: values.provider,
        region: values.provider === "aliyun" ? values.region ?? null : null,
        credential_id: credentialId,
        capabilities: values.capabilities ?? [],
      });
      try {
        const verified = await verifyResourceConnection(connection.id);
        if (verified.status === "verified") {
          message.success("资源连接已创建，只读验证通过");
        } else {
          message.warning("资源连接已创建，但只读验证未通过，请查看状态");
        }
      } catch (verifyError) {
        message.warning(
          verifyError instanceof Error
            ? `资源连接已创建，但验证未执行：${verifyError.message}`
            : "资源连接已创建，但验证未执行",
        );
      }
      onCreated();
      onClose();
    } catch (submitError) {
      // A credential may already exist when connection creation fails (for
      // example a duplicate name). Keep it selectable instead of leaving an
      // orphaned secret behind or asking the operator to paste the key again.
      if (createdCredentialId) {
        setMode("existing");
        form.setFieldValue("credential_id", createdCredentialId);
        await queryClient.invalidateQueries({ queryKey: ["resource-credentials"] });
      }
      setError(submitError);
      if (submitError instanceof ApiError && Object.keys(submitError.fields).length > 0) {
        form.setFields(
          Object.entries(submitError.fields).map(([name, text]) => ({
            name: name as never,
            errors: [text],
          })),
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  const errorMessage =
    error instanceof Error ? error.message : error ? "创建资源连接失败" : null;

  return (
    <Modal
      open={open}
      title="新建资源连接"
      onCancel={onClose}
      forceRender
      width={620}
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={submitting} onClick={() => void submit()}>
            只读验证并保存
          </Button>
        </Space>
      }
    >
      <p className="small muted resource-modal-lead">
        资源连接是租户级凭证，可被多个资源池复用。只读验证会检查供应商、区域和能力，不会创建实例。
      </p>
      {errorMessage ? (
        <Alert
          type="error"
          showIcon
          message={errorMessage}
          description={
            error instanceof ApiError && error.code ? (
              <span className="mono small">
                {error.code}
                {error.details ? ` · ${JSON.stringify(error.details)}` : ""}
              </span>
            ) : null
          }
          style={{ marginBottom: 16 }}
        />
      ) : null}
      <Form form={form} name="resource-connection" layout="vertical" requiredMark="optional">
        <Form.Item
          name="name"
          label="连接名称"
          rules={[{ required: true, message: "请输入连接名称" }]}
        >
          <Input placeholder="Aliyun ECS · Production" maxLength={128} />
        </Form.Item>
        <div className="resource-form-row">
          <Form.Item
            name="provider"
            label="供应商"
            rules={[{ required: true, message: "请选择供应商" }]}
          >
            <Select
              options={[
                { value: "aliyun", label: "阿里云 ECS" },
                {
                  value: "github",
                  label: "GitHub Actions（暂未接入供给器）",
                  disabled: true,
                },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="region"
            label="区域"
            rules={
              provider === "aliyun"
                ? [{ required: true, message: "请输入区域" }]
                : undefined
            }
          >
            <Input placeholder="cn-hangzhou" maxLength={64} />
          </Form.Item>
        </div>
        <Form.Item
          name="capabilities"
          label="能力清单"
          rules={[{ required: true, message: "至少声明一项能力" }]}
          extra="能力决定该连接可用于哪些供给模式；竞价池至少需要 ecs:spot。"
        >
          <Checkbox.Group
            options={[
              { label: "ecs:spot · 创建与释放竞价实例", value: SPOT_CAPABILITY },
              { label: "ecs:inventory · 查询价格与库存", value: INVENTORY_CAPABILITY },
            ]}
          />
        </Form.Item>

        <Form.Item label="接入凭据" required>
          <Radio.Group
            value={mode}
            onChange={(event) => setMode(event.target.value as "existing" | "new")}
            options={[
              { value: "new", label: "新建资源凭据" },
              {
                value: "existing",
                label: "使用已有资源凭据",
                disabled: credentials.length === 0,
              },
            ]}
          />
        </Form.Item>

        {mode === "existing" ? (
          <>
            {credentialsError ? (
              <Alert
                type="warning"
                showIcon
                message="无法加载已有资源凭据"
                description="仍可切换到“新建资源凭据”继续。"
                style={{ marginBottom: 12 }}
              />
            ) : null}
            <Form.Item
              name="credential_id"
              label="资源凭据"
              rules={[{ required: true, message: "请选择资源凭据" }]}
            >
              <Select
                placeholder="选择本租户未撤销的资源凭据"
                options={credentials.map((credential) => ({
                  value: credential.id,
                  label: `${providerLabel(credential.provider)} · ${
                    credential.secret_mask ?? "已脱敏"
                  }`,
                }))}
              />
            </Form.Item>
          </>
        ) : (
          <div className="resource-form-row">
            <Form.Item
              name="access_key_id"
              label="Access Key ID"
              rules={[{ required: true, message: "请输入 Access Key ID" }]}
            >
              <Input placeholder="LTAI…" autoComplete="off" />
            </Form.Item>
            <Form.Item
              name="access_key_secret"
              label="Access Key Secret"
              rules={[{ required: true, message: "请输入 Access Key Secret" }]}
              extra="密钥加密存储，保存后只显示掩码；不会写入浏览器存储。"
            >
              <Input.Password placeholder="••••••••" autoComplete="new-password" />
            </Form.Item>
          </div>
        )}
      </Form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Pool create/edit modal
// ---------------------------------------------------------------------------

interface PoolFormValues {
  name: string;
  connection_id?: string;
  provisioning_mode: ProvisioningMode;
  min_capacity: number;
  max_capacity: number;
  price_threshold_cny?: string | number;
  instance_type?: string;
  runners_per_instance?: number;
  runner_group?: string;
  labels_text?: string;
}

function PoolModal({
  open,
  pool,
  connections,
  initialConnectionId,
  onClose,
  onSaved,
}: {
  open: boolean;
  pool?: ResourcePool;
  connections: ResourceConnection[];
  initialConnectionId?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form] = Form.useForm<PoolFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const connectionId = Form.useWatch("connection_id", form);
  const mode = (Form.useWatch("provisioning_mode", form) ?? "fixed") as ProvisioningMode;
  const compatibility = spotCompatibility(connections, connectionId);
  const spotDisabled = Boolean(compatibility.reason);
  const isEdit = Boolean(pool);

  useEffect(() => {
    if (!open) return;
    setError(null);
    form.resetFields();
    const defaultMode: ProvisioningMode = pool
      ? pool.provisioning_mode === "spot"
        ? "spot"
        : "fixed"
      : initialConnectionId &&
          !spotCompatibility(connections, initialConnectionId).reason
        ? "spot"
        : "fixed";
    form.setFieldsValue({
      name: pool?.name ?? "",
      connection_id: pool?.connection_id ?? initialConnectionId ?? undefined,
      provisioning_mode: defaultMode,
      min_capacity: pool?.min_capacity ?? 0,
      max_capacity: pool?.max_capacity ?? 1,
      price_threshold_cny: pool?.price_threshold_cny ?? "3.6000",
      instance_type: String(pool?.instance_config?.instance_type ?? "ecs.g7.large"),
      runners_per_instance: Number(pool?.instance_config?.runners_per_instance ?? 1),
      runner_group: pool?.runner_group ?? "",
      labels_text: labelList(pool?.labels ?? {}).join(", "),
    });
  }, [form, initialConnectionId, open, pool]);

  useEffect(() => {
    if (mode === "spot" && spotDisabled) {
      form.setFieldValue("provisioning_mode", "fixed");
    }
  }, [form, mode, spotDisabled]);

  async function submit() {
    try {
      const values = await form.validateFields();
      if (values.provisioning_mode === "spot" && spotDisabled) {
        setError(new ApiError(compatibility.reason ?? "竞价模式与连接不兼容", {
          code: "connection_provider_mismatch",
        }));
        return;
      }
      setSubmitting(true);
      setError(null);
      const labels = parseLabels(values.labels_text);
      const instanceConfig: Record<string, unknown> = {};
      if (values.provisioning_mode === "spot") {
        instanceConfig.instance_type = values.instance_type?.trim() || "ecs.g7.large";
        instanceConfig.runners_per_instance = Number(values.runners_per_instance ?? 1);
      }
      const input: PoolWriteInput = {
        name: values.name,
        connection_id: values.connection_id ?? null,
        provisioning_mode: values.provisioning_mode,
        min_capacity: Number(values.min_capacity ?? 0),
        max_capacity: Number(values.max_capacity ?? 1),
        price_threshold_cny:
          values.provisioning_mode === "spot" && values.price_threshold_cny !== undefined
            ? String(values.price_threshold_cny)
            : null,
        instance_config: instanceConfig,
        runner_group: values.runner_group?.trim() || null,
        labels,
      };
      if (pool) {
        await updateResourcePool(pool.id, input);
        message.success("资源池已更新");
      } else {
        await createResourcePool(input);
        message.success("资源池已创建");
      }
      onSaved();
      onClose();
    } catch (submitError) {
      setError(submitError);
      if (submitError instanceof ApiError && Object.keys(submitError.fields).length > 0) {
        form.setFields(
          Object.entries(submitError.fields).map(([name, text]) => ({
            name: name as never,
            errors: [text],
          })),
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      title={isEdit ? `编辑资源池 · ${pool?.name ?? ""}` : "新建资源池"}
      onCancel={onClose}
      forceRender
      width={640}
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            loading={submitting}
            disabled={mode === "spot" && spotDisabled}
            onClick={() => void submit()}
          >
            {isEdit ? "保存修改" : "保存资源池"}
          </Button>
        </Space>
      }
    >
      <p className="small muted resource-modal-lead">
        先选择资源连接，再选择该连接能力支持的供给模式。连接决定可用权限范围；提交时 API 会再次校验连接能力，不匹配会在创建实例前返回
        connection_provider_mismatch。
      </p>
      {error ? (
        <Alert
          type="error"
          showIcon
          message={error instanceof Error ? error.message : "保存资源池失败"}
          description={
            error instanceof ApiError && (error.code || error.details) ? (
              <span className="mono small">
                {[error.code, error.details ? JSON.stringify(error.details) : null]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            ) : null
          }
          style={{ marginBottom: 16 }}
        />
      ) : null}
      <Form form={form} name="resource-pool" layout="vertical" requiredMark="optional">
        <Form.Item
          name="connection_id"
          label="资源连接"
          extra="自有机器注册可以不使用资源连接。"
        >
          <Select
            allowClear
            placeholder="不使用连接（自有机器注册）"
            options={connections.map((connection) => ({
              value: connection.id,
              label: `${connection.name} · ${providerLabel(connection.provider)}${
                connection.status === "failed" ? "（验证失败）" : ""
              }`,
            }))}
          />
        </Form.Item>

        {compatibility.connection ? (
          <div className="connection-capability-panel">
            <div className="row-between">
              <strong>{compatibility.connection.name}</strong>
              {statusTag(compatibility.connection.status, connectionStatusMeta)}
            </div>
            <div className="capability-tags">
              {compatibility.connection.capabilities.map((capability) => (
                <span className="tag tag-neutral mono" key={capability}>
                  {capability}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <Form.Item
          name="provisioning_mode"
          label="供给模式"
          rules={[{ required: true, message: "请选择供给模式" }]}
        >
          <Radio.Group>
            <Radio value="fixed">自有机器注册（固定 Runner）</Radio>
            <Radio value="spot" disabled={spotDisabled}>
              竞价实例 ECS
            </Radio>
          </Radio.Group>
        </Form.Item>
        <div className="small muted resource-mode-note">
          固定 Runner 组、按量付费、包年包月与 ECI 供给模式为后续里程碑预留；当前仅启用自有机器注册与竞价实例。
        </div>
        {mode === "spot" ? (
          spotDisabled ? (
            <div className="banner warning resource-reason">
              <Icon name="lock" size={15} />
              <div>
                <strong>竞价实例暂不可选。</strong> {compatibility.reason}
              </div>
            </div>
          ) : (
            <div className="banner success resource-reason">
              <Icon name="check" size={15} />
              <div>
                当前组合为 {providerLabel(compatibility.connection?.provider ?? "fixed")} ·{" "}
                {modeLabel(mode)}，提交后仍由 API 做最终能力校验。
              </div>
            </div>
          )
        ) : (
          <div className="banner resource-reason">
            <Icon name="server" size={15} />
            <div>
              自有机器注册不使用云供给器：在机器上运行 Runner 注册脚本并打上池标签即可接入。
              {spotDisabled ? `需要竞价扩容时：${compatibility.reason}。` : ""}
            </div>
          </div>
        )}

        <div className="resource-form-row">
          <Form.Item
            name="name"
            label="池名称"
            rules={[{ required: true, message: "请输入池名称" }]}
          >
            <Input placeholder="Linux Spot Pool" maxLength={128} />
          </Form.Item>
          <Form.Item
            name="max_capacity"
            label="容量上限（Runner）"
            rules={[{ required: true, message: "请输入容量上限" }]}
          >
            <InputNumber min={1} max={256} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <div className="resource-form-row">
          <Form.Item name="min_capacity" label="常驻下限">
            <InputNumber min={0} max={256} style={{ width: "100%" }} />
          </Form.Item>
          {mode === "spot" ? (
            <Form.Item
              name="price_threshold_cny"
              label="竞价价格阈值（CNY/小时）"
              extra="当前价格高于阈值时停止扩容，排队 Job 保留。"
            >
              <InputNumber min={0.0001} step={0.01} style={{ width: "100%" }} />
            </Form.Item>
          ) : (
            <Form.Item name="runner_group" label="Runner 组（可选）">
              <Input placeholder="固定 Runner 组" maxLength={128} />
            </Form.Item>
          )}
        </div>
        {mode === "spot" ? (
          <div className="resource-form-row">
            <Form.Item name="instance_type" label="实例规格">
              <Input placeholder="ecs.g7.large" maxLength={64} />
            </Form.Item>
            <Form.Item
              name="runners_per_instance"
              label="单实例 Runner 数"
              extra="GitHub Runner 默认一个 Runner 同时只执行一个 Job。"
            >
              <InputNumber min={1} max={64} style={{ width: "100%" }} />
            </Form.Item>
          </div>
        ) : null}
        <Form.Item name="labels_text" label="Runner 标签" extra="用逗号分隔，例如 self-hosted, linux, eval">
          <Input placeholder="self-hosted, linux, eval" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Pool detail drawer
// ---------------------------------------------------------------------------

const runnerColumns: TableColumnsType<PoolRunner> = [
  {
    title: "Runner",
    dataIndex: "name",
    key: "name",
    render: (_value, runner) => (
      <div>
        <div className="mono">{runner.name}</div>
        <div className="small muted">{runner.busy_case_attempt_id ?? "无运行中 Job"}</div>
      </div>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    render: (value: string) => statusTag(value, runnerStatusMeta),
  },
  {
    title: "标签",
    dataIndex: "labels",
    key: "labels",
    render: (value: unknown) => (
      <div className="capability-tags">
        {labelList(value).slice(0, 6).map((label) => (
          <span className="tag tag-neutral mono" key={label}>
            {label}
          </span>
        ))}
      </div>
    ),
  },
  {
    title: "最近心跳",
    dataIndex: "last_heartbeat_at",
    key: "last_heartbeat_at",
    render: (value: string | null) => (
      <span title={formatDateTime(value)}>{relativeTime(value)}</span>
    ),
  },
];

function eventSummary(event: { event_type: string; payload: Record<string, unknown> }): string {
  const payload = event.payload;
  const parts: string[] = [];
  if (typeof payload.reason === "string") parts.push(`原因：${payload.reason}`);
  if (typeof payload.code === "string") parts.push(`错误：${payload.code}`);
  if (typeof payload.message === "string") parts.push(payload.message);
  if (typeof payload.instance_id === "string") parts.push(`实例 ${payload.instance_id}`);
  if (typeof payload.provider_instance_id === "string") {
    parts.push(`供应商实例 ${payload.provider_instance_id}`);
  }
  if (typeof payload.price_cny === "string") parts.push(`价格 ${formatUnitPrice(payload.price_cny)}`);
  if (typeof payload.threshold === "string") parts.push(`阈值 ${formatUnitPrice(payload.threshold)}`);
  if (parts.length === 0) return "—";
  return parts.join(" · ");
}

function PoolDetailDrawer({
  open,
  poolId,
  connections,
  isAdmin,
  onClose,
  onEdit,
  onTest,
}: {
  open: boolean;
  poolId: string | null;
  connections: ResourceConnection[];
  isAdmin: boolean;
  onClose: () => void;
  onEdit: (pool: ResourcePool) => void;
  onTest: (poolId: string) => void;
}) {
  const detailQuery = useQuery({
    queryKey: ["resource-pool", poolId],
    queryFn: ({ signal }) => fetchResourcePool(poolId ?? "", signal),
    enabled: open && Boolean(poolId),
    refetchInterval: open ? 15000 : false,
  });
  const runnersQuery = useQuery({
    queryKey: ["resource-pool", poolId, "runners"],
    queryFn: ({ signal }) => fetchPoolRunners(poolId ?? "", signal),
    enabled: open && Boolean(poolId),
    refetchInterval: open ? 15000 : false,
  });
  const detail: ResourcePoolDetail | undefined = detailQuery.data;
  const connection = detail ? connectionOf(connections, detail.connection_id) : undefined;
  const state = detail?.capacity_state;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={720}
      title={detail ? detail.name : "资源池详情"}
      extra={
        detail ? (
          <Space size={8}>
            <Button
              size="small"
              icon={<Icon name="refresh" size={13} />}
              onClick={() => {
                void detailQuery.refetch();
                void runnersQuery.refetch();
              }}
              loading={detailQuery.isFetching || runnersQuery.isFetching}
            >
              刷新
            </Button>
            {isAdmin ? (
              <Button size="small" onClick={() => onEdit(detail)}>
                编辑
              </Button>
            ) : null}
            {isAdmin && detail.provisioning_mode === "spot" ? (
              <Button size="small" type="primary" onClick={() => onTest(detail.id)}>
                测试供给
              </Button>
            ) : null}
          </Space>
        ) : null
      }
    >
      {detailQuery.isError && !detail ? (
        <QueryError
          error={detailQuery.error}
          onRetry={() => void detailQuery.refetch()}
          isRetrying={detailQuery.isFetching}
          title="无法加载资源池详情"
          endpoint={`GET /api/v1/resource-pools/${poolId ?? ""}`}
        />
      ) : null}
      {!detail && !detailQuery.isError ? <Skeleton active /> : null}
      {detail && state ? (
        <>
          <div className="pool-head detail-head">
            <div
              className={`pool-icon${detail.provisioning_mode === "spot" ? " spot" : ""}`}
            >
              <Icon name={detail.provisioning_mode === "spot" ? "cloud" : "server"} size={18} />
            </div>
            <div className="pool-title">
              <div className="pool-name">
                {providerLabel(connection?.provider ?? detail.provider_key)} ·{" "}
                {modeLabel(detail.provisioning_mode)}
              </div>
              <div className="small muted">
                {connection ? connection.name : "未绑定资源连接"}
                {detail.runner_group ? ` · Runner 组 ${detail.runner_group}` : ""}
              </div>
            </div>
            {tagBadge(poolStatusMeta(detail))}
          </div>

          <div className="pool-stats detail-stats">
            <PoolStat label="在线 Runner" value={liveRunners(detail.runners)} />
            <PoolStat label="忙碌" value={detail.runners.busy} />
            <PoolStat label="池上限" value={detail.max_capacity} />
            <PoolStat label="扩容中" value={state.starting_instances} />
            <PoolStat label="排队 Job" value={state.queued_attempts} />
            <PoolStat label="当前缺口" value={state.capacity_gap} />
          </div>

          <div className="detail-meta-grid">
            <PoolMeta label="容量区间" value={`${detail.min_capacity} – ${detail.max_capacity}`} />
            <PoolMeta
              label="当前价格 / 阈值（CNY/小时）"
              value={`${formatUnitPrice(state.price_cny)} / ${formatUnitPrice(
                state.price_threshold_cny ?? detail.price_threshold_cny,
              )}`}
            />
            <PoolMeta label="最近心跳" value={relativeTime(detail.runners.last_heartbeat_at)} />
            <PoolMeta
              label="最近检查"
              value={formatDateTime(state.last_checked_at ?? detail.last_checked_at)}
            />
            <PoolMeta
              label="下次检查"
              value={formatDateTime(state.next_check_at ?? detail.next_check_at)}
            />
            <PoolMeta
              label="离线 / 排空"
              value={`${detail.runners.offline} / ${detail.runners.draining}`}
            />
            <PoolMeta
              label="当前利用率"
              value={`${Math.round(detail.usage.utilization.ratio * 100)}%`}
            />
            <PoolMeta
              label="今日资源成本"
              value={formatMoney(detail.usage.cost.today_cny)}
            />
          </div>

          {state.capacity_reason ? (
            <div
              className={`banner ${
                capacityReasonMeta[state.capacity_reason]?.tone === "danger"
                  ? "danger"
                  : capacityReasonMeta[state.capacity_reason]?.tone === "warning"
                    ? "warning"
                    : "success"
              } resource-reason`}
              role="status"
            >
              <Icon
                name={capacityReasonMeta[state.capacity_reason]?.tone === "info" ? "pulse" : "alert"}
                size={15}
              />
              <div>
                <strong>
                  {capacityReasonMeta[state.capacity_reason]?.title ?? state.capacity_reason}
                </strong>{" "}
                {capacityReasonMeta[state.capacity_reason]?.describe(state) ??
                  "请查看事件与供给器诊断。"}
              </div>
            </div>
          ) : null}

          <div className="detail-section">
            <h3 className="detail-section-title">Runner 明细</h3>
            <Table<PoolRunner>
              rowKey="id"
              size="small"
              columns={runnerColumns}
              dataSource={runnersQuery.data ?? []}
              loading={runnersQuery.isFetching}
              pagination={false}
              locale={{
                emptyText: (
                  <EmptyState
                    compact
                    inline
                    icon="server"
                    title="暂无 Runner"
                    description="扩容创建实例并注册 Runner 后会出现在这里。"
                  />
                ),
              }}
            />
          </div>

          <div className="detail-section">
            <h3 className="detail-section-title">最近资源事件</h3>
            {detail.events.length === 0 ? (
              <EmptyState
                compact
                inline
                icon="pulse"
                title="暂无资源事件"
                description="容量检查、扩容、释放与供给测试会记录在这里。"
              />
            ) : (
              <Timeline
                items={detail.events.map((event) => ({
                  color:
                    event.event_type.includes("failed") ||
                    event.event_type.includes("interrupted")
                      ? "red"
                      : event.event_type.includes("released") ||
                          event.event_type.includes("tested") ||
                          event.event_type.includes("registered")
                        ? "green"
                        : "blue",
                  children: (
                    <div>
                      <div className="row-between">
                        <strong>
                          {eventTypeLabels[event.event_type] ?? event.event_type}
                        </strong>
                        <span className="small muted">
                          {formatDateTime(event.created_at)}
                        </span>
                      </div>
                      <div className="small muted">{eventSummary(event)}</div>
                    </div>
                  ),
                }))}
              />
            )}
          </div>

          <div className="detail-section">
            <h3 className="detail-section-title">池配置</h3>
            <div className="detail-meta-grid">
              <PoolMeta
                label="实例规格"
                value={String(detail.instance_config.instance_type ?? "—")}
              />
              <PoolMeta
                label="单实例 Runner 数"
                value={String(detail.instance_config.runners_per_instance ?? 1)}
              />
              <PoolMeta
                label="Runner 标签"
                value={labelList(detail.labels).join(", ") || "未设置"}
              />
              <PoolMeta
                label="连接能力"
                value={
                  connection ? connection.capabilities.join(", ") || "未声明" : "未绑定连接"
                }
              />
            </div>
          </div>
        </>
      ) : null}
    </Drawer>
  );
}

// ---------------------------------------------------------------------------
// Provisioning test modal
// ---------------------------------------------------------------------------

function stepDetail(step: ProvisioningStep): string {
  const detail = step.detail;
  if (step.key === "quote") {
    return `价格 ${formatUnitPrice(String(detail.price_cny ?? ""))}，阈值 ${formatUnitPrice(
      String(detail.price_threshold_cny ?? ""),
    )}；${detail.region ?? "—"} · ${detail.instance_type ?? "—"}`;
  }
  if (step.key === "instance_created") {
    return `实例 ${detail.provider_instance_id ?? detail.instance_id ?? "—"} 已创建`;
  }
  if (step.key === "runner_registration") {
    return String(detail.note ?? detail.reason ?? "未注册 Runner");
  }
  if (step.key === "instance_released") {
    if (step.status === "failed") {
      return `释放失败：${detail.code ?? "unknown"}${detail.message ? ` · ${detail.message}` : ""}`;
    }
    return `实例状态 ${detail.status ?? "released"}，平台已自动释放，避免持续计费`;
  }
  return `已确认（confirm_cost=${String(detail.confirm_cost ?? true)}）`;
}

function provisioningStepStatus(step: ProvisioningStep): string {
  if (step.status === "passed") return "green";
  if (step.status === "failed") return "red";
  return "gray";
}

function ProvisioningTestModal({
  open,
  pool,
  onClose,
  onFinished,
}: {
  open: boolean;
  pool: ResourcePool | null;
  onClose: () => void;
  onFinished: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ProvisioningTestResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const state = pool?.capacity_state;
  const spot = pool?.provisioning_mode === "spot";

  useEffect(() => {
    if (!open) return;
    setConfirmed(false);
    setRunning(false);
    setResult(null);
    setError(null);
  }, [open, pool?.id]);

  async function run() {
    if (!pool) return;
    setRunning(true);
    setError(null);
    try {
      const outcome = await testProvisioning(pool.id);
      setResult(outcome);
      if (outcome.released) {
        message.success("供给测试完成：实例已创建并自动释放，审计已记录");
      } else {
        message.warning("实例已创建，但自动释放失败；已交给容量控制器重试");
      }
      onFinished();
    } catch (runError) {
      setError(runError);
    } finally {
      setRunning(false);
    }
  }

  function close() {
    if (running) return;
    onClose();
  }

  return (
    <Modal
      open={open}
      title="测试供给"
      onCancel={close}
      width={620}
      maskClosable={!running}
      footer={
        <Space>
          {!result ? (
            <Button onClick={close} disabled={running}>
              取消
            </Button>
          ) : null}
          {result ? (
            <Button type="primary" onClick={close}>
              完成
            </Button>
          ) : (
            <Button
              type="primary"
              danger
              loading={running}
              disabled={!confirmed || !spot || !pool}
              onClick={() => void run()}
            >
              确认并测试供给
            </Button>
          )}
        </Space>
      }
    >
      {!spot ? (
        <Alert
          type="warning"
          showIcon
          message="只有竞价资源池支持测试供给"
          description="固定 Runner 池由已有机器注册，不经过云供给器。"
        />
      ) : (
        <>
          <div className="banner warning resource-note" role="alert">
            <Icon name="alert" size={15} />
            <div>
              <strong>此操作会创建真实云实例并产生费用。</strong>{" "}
              测试实例创建后立即自动释放，不承接任何评测；失败时保留可诊断错误码与事件。
            </div>
          </div>
          <div className="detail-meta-grid provisioning-facts">
            <PoolMeta label="资源池" value={pool?.name ?? "—"} />
            <PoolMeta
              label="区域 / 规格"
              value={`按连接区域 / ${String(pool?.instance_config.instance_type ?? "ecs.g7.large")}`}
            />
            <PoolMeta label="当前价格（CNY/小时）" value={formatUnitPrice(state?.price_cny ?? pool?.price_cny)} />
            <PoolMeta
              label="价格阈值（CNY/小时）"
              value={formatUnitPrice(state?.price_threshold_cny ?? pool?.price_threshold_cny)}
            />
            <PoolMeta label="最长存活" value="创建后立即释放" />
            <PoolMeta label="审计" value="管理员操作写入审计日志" />
          </div>
          <Checkbox
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            disabled={running || Boolean(result)}
          >
            我了解测试供给会创建真实实例并产生费用，测试实例不会承接评测
          </Checkbox>
        </>
      )}

      {error ? (
        <div className="provisioning-result">
          <Alert
            type="error"
            showIcon
            message={error instanceof Error ? error.message : "测试供给失败"}
            description={
              error instanceof ApiError ? (
                <div className="stack">
                  <span className="mono small">
                    {[error.code, error.status ? `HTTP ${error.status}` : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                  {Object.keys(error.fields).length > 0 ? (
                    <span className="small">{JSON.stringify(error.fields)}</span>
                  ) : null}
                  {error.details ? (
                    <span className="mono small">{JSON.stringify(error.details)}</span>
                  ) : null}
                  <span className="small muted">
                    失败结果已保留在资源事件中，可再次测试或到详情抽屉查看诊断。
                  </span>
                </div>
              ) : null
            }
          />
        </div>
      ) : null}

      {result ? (
        <div className="provisioning-result">
          <div className="row-between">
            <strong>从实例创建到 Runner 注册</strong>
            <span className={`tag ${result.released ? "tag-success" : "tag-warning"}`}>
              {result.released ? "实例已自动释放" : "释放重试中"}
            </span>
          </div>
          <Timeline
            className="provisioning-timeline"
            items={result.steps.map((step) => ({
              color: provisioningStepStatus(step),
              children: (
                <div>
                  <div className="row-between">
                    <strong>{stepLabels[step.key] ?? step.key}</strong>
                    <span className="small muted">
                      {step.status === "passed"
                        ? "成功"
                        : step.status === "failed"
                          ? "失败"
                          : "跳过"}
                    </span>
                  </div>
                  <div className="small muted">{stepDetail(step)}</div>
                </div>
              ),
            }))}
          />
          <div className="small muted">
            测试实例不注册 Runner；正式扩容时，供给器会在实例就绪后注册带池标签的临时
            Runner，本页会通过容量与 Runner 明细继续展示。
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

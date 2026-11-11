import { useMemo, useState } from "react";
import { Button, Checkbox, Modal, Skeleton, Table, message } from "antd";
import type { TableColumnsType } from "antd";
import { useDeploymentsQuery, useRollbackMutation } from "../api/deployments";
import type { DeploymentItem, HealthComponent } from "../api/deployments";
import { ApiError } from "../api/types";
import { useAuth } from "../app/auth";
import { EmptyState } from "../components/EmptyState";
import { Icon, type IconName } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { StatusCard } from "../components/StatusCard";

const statusMeta: Record<string, { label: string; tone: string }> = {
  succeeded: { label: "部署成功", tone: "success" },
  deploying: { label: "部署中", tone: "primary" },
  failed: { label: "部署失败", tone: "danger" },
  rolled_back: { label: "已回滚", tone: "warning" },
};

const stageStatusMeta: Record<string, { label: string; icon: IconName; tone: string }> = {
  success: { label: "成功", icon: "check", tone: "success" },
  running: { label: "进行中", icon: "refresh", tone: "running" },
  failed: { label: "失败", icon: "alert", tone: "failed" },
  unknown: { label: "记录缺失", icon: "help", tone: "unknown" },
  skipped: { label: "跳过", icon: "help", tone: "skipped" },
};

const componentStatusMeta: Record<string, { label: string; tone: string }> = {
  ok: { label: "健康", tone: "success" },
  degraded: { label: "降级", tone: "warning" },
  error: { label: "异常", tone: "danger" },
  disabled: { label: "未配置", tone: "neutral" },
  unknown: { label: "未知", tone: "neutral" },
};

const componentIcons: Record<string, IconName> = {
  api: "cloud",
  database: "database",
  redis: "cpu",
  object_storage: "boxes",
  worker: "pulse",
};

function StatusTag({ status, labels }: {
  status: string;
  labels: Record<string, { label: string; tone: string }>;
}) {
  const meta = labels[status] ?? { label: status || "未知", tone: "neutral" };
  return (
    <span className={`tag tag-${meta.tone}`}>
      <span className="status-dot" aria-hidden="true" />
      {meta.label}
    </span>
  );
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) {
    return "—";
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`;
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

function DeployTimeline({ deployment }: { deployment: DeploymentItem }) {
  return (
    <div className="deploy-timeline">
      {deployment.stages.map((stage) => {
        const meta = stageStatusMeta[stage.status] ?? stageStatusMeta.unknown;
        return (
          <div className="deploy-step" key={stage.key}>
            <div className={`deploy-icon ${meta.tone}`} aria-hidden="true">
              <Icon name={meta.icon} size={13} />
            </div>
            <div className="deploy-step-copy">
              <div className="deploy-title">{stage.label}</div>
              <div className="deploy-meta">
                {stage.detail || meta.label}
              </div>
            </div>
            <div className="deploy-step-side">
              {stage.status === "success" || stage.status === "running" ? (
                <span className="small muted mono">
                  {stage.status === "running"
                    ? "进行中"
                    : stage.duration_seconds === null
                      ? ""
                      : formatDuration(stage.duration_seconds)}
                </span>
              ) : (
                <span className={`tag tag-${meta.tone === "failed" ? "danger" : "neutral"}`}>
                  {meta.label}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function EnvironmentCard({ components }: { components: HealthComponent[] }) {
  return (
    <section className="card">
      <div className="card-header">
        <div>
          <h2 className="card-title">环境</h2>
          <p className="card-subtitle">生产组件健康摘要</p>
        </div>
      </div>
      <div className="card-body stack deploy-env-list">
        {components.map((component) => (
          <div className="row-between deploy-env-row" key={component.key}>
            <span className="row deploy-env-name">
              <Icon name={componentIcons[component.key] ?? "server"} size={15} />
              {component.name}
            </span>
            <span className="deploy-env-status" title={component.summary}>
              <StatusTag status={component.status} labels={componentStatusMeta} />
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function RollbackCard({
  deployment,
  rollback,
  isAdmin,
  onRequest,
}: {
  deployment: DeploymentItem | null;
  rollback: {
    available: boolean;
    reason: string;
    message: string;
    database_revision: string | null;
    target: DeploymentItem | null;
  };
  isAdmin: boolean;
  onRequest: () => void;
}) {
  return (
    <section className="card">
      <div className="card-header">
        <div>
          <h2 className="card-title">回滚策略</h2>
        </div>
        <span className="tag tag-primary">自动 + 受控</span>
      </div>
      <div className="card-body deploy-rollback-body">
        <p className="small muted deploy-rollback-copy">
          迁移成功而健康检查失败时，Compose 自动恢复上一版镜像；数据库只执行向前兼容脚本，破坏性变更拆分为两个版本。手动回滚仅替换应用镜像，不执行数据库
          downgrade。
        </p>
        <div className="deploy-rollback-tip">
          <Icon name="shield" size={14} />
          <span>
            数据库保持当前版本
            {rollback.database_revision ? (
              <span className="mono"> {rollback.database_revision}</span>
            ) : null}
            ，已产生的云资源与评测费用不会因回滚退还。
          </span>
        </div>
        {isAdmin ? (
          rollback.available && rollback.target && deployment ? (
            <Button
              danger
              block
              className="deploy-rollback-button"
              onClick={onRequest}
              icon={<Icon name="refresh" size={14} />}
            >
              回滚到 {rollback.target.short_commit}
            </Button>
          ) : (
            <p className="small muted deploy-rollback-note">{rollback.message}</p>
          )
        ) : (
          <p className="small muted deploy-rollback-note">仅平台管理员可执行回滚。</p>
        )}
      </div>
    </section>
  );
}

function RollbackModal({
  open,
  target,
  current,
  submitting,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  target: DeploymentItem | null;
  current: DeploymentItem | null;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: (reason?: string) => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [reason, setReason] = useState("");

  const close = () => {
    setAcknowledged(false);
    setReason("");
    onCancel();
  };

  return (
    <Modal
      open={open}
      title={target ? `回滚到 ${target.short_commit}？` : "回滚到上一版本？"}
      onCancel={close}
      destroyOnHidden
      footer={[
        <Button key="cancel" onClick={close}>
          取消
        </Button>,
        <Button
          key="confirm"
          danger
          type="primary"
          disabled={!acknowledged || submitting}
          loading={submitting}
          onClick={() => {
            onConfirm(reason);
            setAcknowledged(false);
            setReason("");
          }}
        >
          确认回滚
        </Button>,
      ]}
    >
      <div className="deploy-danger-banner" role="alert">
        <div className="deploy-danger-icon" aria-hidden="true">
          <Icon name="alert" size={20} />
        </div>
        <div>
          <strong>这是一次不可逆的生产操作</strong>
          <p className="small">
            应用镜像会替换为上一成功版本；回滚操作本身会写入审计记录。
          </p>
        </div>
      </div>
      <ul className="deploy-impact-list">
        <li>
          替换当前 <span className="mono">{current?.short_commit ?? "—"}</span>{" "}
          的 API / Web 容器，通常需要 40–60 秒。
        </li>
        <li>
          不执行数据库 downgrade；数据库保持当前迁移版本
          <span className="mono"> {current?.migration_revision ?? "—"}</span>。
        </li>
        <li>已开始的评测会短暂暂停并重新排队，不会丢失用例。</li>
        <li>已产生的云资源、Runner 与评测费用不会因回滚退还。</li>
      </ul>
      <label className="deploy-confirm-row">
        <Checkbox
          checked={acknowledged}
          onChange={(event) => setAcknowledged(event.target.checked)}
        />
        <span>我已了解上述影响，确认回滚到 {target?.short_commit ?? "上一版本"}</span>
      </label>
      <label className="deploy-reason-row">
        <span className="small muted">回滚原因（可选）</span>
        <textarea
          className="deploy-reason-input"
          value={reason}
          maxLength={500}
          rows={2}
          placeholder="例如：健康检查连续失败"
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
    </Modal>
  );
}

export function DeploymentsPage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useDeploymentsQuery();
  const { session } = useAuth();
  const rollbackMutation = useRollbackMutation();
  const [messageApi, contextHolder] = message.useMessage();
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const isAdmin = session?.role === "admin";

  const historyColumns = useMemo<TableColumnsType<DeploymentItem>>(
    () => [
      {
        title: "版本",
        dataIndex: "short_commit",
        key: "version",
        render: (_value: string, row) => (
          <div className="deploy-version-cell">
            <a
              className="table-primary mono deploy-version-link"
              href={row.commit_url}
              target="_blank"
              rel="noreferrer"
              title={row.git_commit}
            >
              {row.short_commit}
            </a>
            <div className="deploy-version-meta">
              <span>main</span>
              {row.is_rollback ? <span className="tag tag-warning">回滚</span> : null}
            </div>
          </div>
        ),
      },
      {
        title: "触发者",
        dataIndex: "triggered_by",
        key: "triggered_by",
        render: (value: string | null) =>
          value ? <span>@{value}</span> : <span className="muted">—</span>,
      },
      {
        title: "数据库版本",
        dataIndex: "migration_revision",
        key: "migration_revision",
        render: (value: string) => <span className="mono">{value || "—"}</span>,
      },
      {
        title: "耗时",
        dataIndex: "duration_seconds",
        key: "duration",
        render: (value: number | null) => (
          <span className="mono">{formatDuration(value)}</span>
        ),
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        render: (value: string) => <StatusTag status={value} labels={statusMeta} />,
      },
      {
        title: "",
        key: "link",
        width: 104,
        render: (_value: unknown, row) => (
          <a
            className="deploy-inline-link"
            href={row.run_url ?? row.commit_url}
            target="_blank"
            rel="noreferrer"
          >
            {row.run_url ? "查看运行" : "查看提交"}
            <Icon name="arrowRight" size={13} />
          </a>
        ),
      },
    ],
    [],
  );

  const submitRollback = (reason?: string) => {
    const target = data?.rollback.target;
    if (!target) {
      return;
    }
    rollbackMutation.mutate(
      { deploymentId: target.id, reason },
      {
        onSuccess: () => {
          setRollbackOpen(false);
          messageApi.success("回滚已触发，部署流水线完成后会更新部署历史。");
        },
        onError: (mutationError) => {
          messageApi.error(
            mutationError instanceof ApiError
              ? mutationError.message
              : "回滚请求失败，请稍后重试。",
          );
        },
      },
    );
  };

  const actions = data ? (
    <Button
      href={data.github.actions_url}
      target="_blank"
      rel="noreferrer"
      icon={<Icon name="git" size={14} />}
    >
      查看 GitHub Actions
    </Button>
  ) : undefined;

  if (isLoading) {
    return (
      <div className="page">
        {contextHolder}
        <PageHeading
          eyebrow="Delivery pipeline"
          title="交付流水线"
          description="main 合入后自动完成镜像构建、数据库迁移、ECS 滚动更新与健康检查。"
        />
        <div className="grid grid-4">
          {[0, 1, 2, 3].map((index) => (
            <StatusCard
              key={index}
              label="加载中"
              value="—"
              icon="pipeline"
              loading
            />
          ))}
        </div>
        <div className="grid dashboard-grid section-gap">
          <section className="card deploy-skeleton-card" aria-busy="true">
            <Skeleton active title paragraph={{ rows: 6 }} />
          </section>
          <section className="card deploy-skeleton-card" aria-busy="true">
            <Skeleton active title paragraph={{ rows: 4 }} />
          </section>
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="page">
        {contextHolder}
        <PageHeading
          eyebrow="Delivery pipeline"
          title="交付流水线"
          description="main 合入后自动完成镜像构建、数据库迁移、ECS 滚动更新与健康检查。"
        />
        <QueryError
          error={error ?? new Error("控制面没有返回交付流水线数据")}
          onRetry={() => void refetch()}
          isRetrying={isFetching}
          title="无法加载交付流水线"
          endpoint="GET /api/v1/deployments"
        />
      </div>
    );
  }

  const current = data.current;
  const latest = data.latest;
  const health = data.health;
  const rollback = data.rollback;
  const healthNames = health.components.map((component) => component.name).join(" · ");

  return (
    <div className="page">
      {contextHolder}
      <PageHeading
        eyebrow="Delivery pipeline"
        title="交付流水线"
        description="main 合入后自动完成镜像构建、数据库迁移、ECS 滚动更新与健康检查。"
        actions={actions}
      />

      <div className="grid grid-4">
        <StatusCard
          label="生产版本"
          value={current?.short_commit ?? "—"}
          meta={current ? `部署于 ${relativeTime(current.started_at)}` : "尚无成功部署"}
          icon="pipeline"
          tone={current ? "success" : "primary"}
        />
        <StatusCard
          label="数据库版本"
          value={
            data.database.revision ? (
              <span className="deploy-db-value" title={data.database.revision}>
                {data.database.revision}
              </span>
            ) : (
              "—"
            )
          }
          meta={
            !current
              ? "尚无成功部署"
              : data.database.revision === null
                ? "无法读取 Alembic 版本"
                : data.database.in_sync
                  ? `部署记录 ${data.database.deployment_revision ?? "—"} · 一致`
                  : `部署记录 ${data.database.deployment_revision ?? "—"} · 不一致`
          }
          icon="database"
          tone={current && data.database.in_sync === false ? "warning" : "primary"}
        />
        <StatusCard
          label="健康检查"
          value={`${health.healthy} / ${health.total}`}
          meta={healthNames}
          icon="shield"
          tone={health.status === "ok" ? "success" : "warning"}
        />
        <StatusCard
          label="最近构建"
          value={latest ? formatDuration(latest.duration_seconds) : "—"}
          meta={latest ? `main@${latest.short_commit}` : "尚无构建记录"}
          icon="clock"
          tone="warning"
        />
      </div>

      <div className="grid dashboard-grid section-gap">
        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                {current ? `当前部署 · ${current.short_commit}` : "当前部署"}
              </h2>
              <p className="card-subtitle">
                {current
                  ? `main@${current.short_commit} · 由 ${
                      current.triggered_by ? `@${current.triggered_by}` : "系统"
                    } 触发 · ${relativeTime(current.started_at)}`
                  : "等待第一次成功的生产部署"}
              </p>
            </div>
            {current ? (
              <span className="card-actions">
                <StatusTag status={current.status} labels={statusMeta} />
              </span>
            ) : null}
          </div>
          <div className="card-body">
            {current ? (
              <DeployTimeline deployment={current} />
            ) : (
              <EmptyState
                icon="pipeline"
                title="还没有成功部署记录"
                description="main 合入后 GitHub Actions 会自动部署，并把当前版本、迁移版本和五阶段流水线写入这里。"
                action={
                  <Button
                    href={data.github.actions_url}
                    target="_blank"
                    rel="noreferrer"
                    icon={<Icon name="git" size={14} />}
                  >
                    查看 GitHub Actions
                  </Button>
                }
              />
            )}
          </div>
        </section>
        <aside className="stack">
          <EnvironmentCard components={health.components} />
          <RollbackCard
            deployment={current}
            rollback={rollback}
            isAdmin={isAdmin}
            onRequest={() => setRollbackOpen(true)}
          />
        </aside>
      </div>

      <section className="card section-gap">
        <div className="card-header">
          <div>
            <h2 className="card-title">部署历史</h2>
            <p className="card-subtitle">仅平台管理员可执行回滚</p>
          </div>
          {data.has_more ? (
            <span className="card-actions small muted">仅显示最近 20 条</span>
          ) : null}
        </div>
        <div className="deploy-history-table">
          <Table<DeploymentItem>
            rowKey="id"
            columns={historyColumns}
            dataSource={data.items}
            pagination={false}
            size="middle"
            locale={{
              emptyText: (
                <EmptyState
                  inline
                  icon="pipeline"
                  title="还没有部署记录"
                  description="部署流水线完成后，历史记录会出现在这里。"
                />
              ),
            }}
          />
        </div>
      </section>

      <RollbackModal
        open={rollbackOpen}
        target={rollback.target}
        current={current}
        submitting={rollbackMutation.isPending}
        onCancel={() => setRollbackOpen(false)}
        onConfirm={submitRollback}
      />
    </div>
  );
}

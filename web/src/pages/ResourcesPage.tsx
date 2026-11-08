import { Button, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { useRunnersQuery, type RunnerItem } from "../api/runners";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { StatusCard } from "../components/StatusCard";

const statusMeta: Record<string, { label: string; color: string }> = {
  online: { label: "在线", color: "success" },
  busy: { label: "忙碌", color: "processing" },
  offline: { label: "离线", color: "default" },
};

function formatHeartbeat(value: string | null): string {
  if (!value) return "从未";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return "未知";
  const seconds = Math.max(0, Math.round((Date.now() - time) / 1000));
  if (seconds < 5) return "刚刚";
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  return new Date(value).toLocaleString("zh-CN");
}

const columns: TableColumnsType<RunnerItem> = [
  {
    title: "Runner",
    dataIndex: "name",
    key: "name",
    render: (_value: string, row) => (
      <div>
        <div className="table-primary">{row.name}</div>
        <div className="table-secondary mono">{row.id}</div>
      </div>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    render: (value: string) => {
      const meta = statusMeta[value] ?? { label: value, color: "default" };
      return (
        <Tag color={meta.color} bordered={false}>
          {meta.label}
        </Tag>
      );
    },
  },
  {
    title: "标签",
    dataIndex: "labels",
    key: "labels",
    render: (labels: string[]) => (
      <div className="runner-labels">
        {labels.length === 0 ? <span className="muted">无标签</span> : labels.map((label) => <Tag key={label}>{label}</Tag>)}
      </div>
    ),
  },
  {
    title: "最近心跳",
    dataIndex: "last_heartbeat_at",
    key: "last_heartbeat_at",
    render: (value: string | null) => <span>{formatHeartbeat(value)}</span>,
  },
  {
    title: "当前任务",
    dataIndex: "current_case_attempt_id",
    key: "current_case_attempt_id",
    render: (value: string | null) => (value ? <span className="mono">{value}</span> : <span className="muted">—</span>),
  },
];

export function ResourcesPage() {
  const query = useRunnersQuery();
  const { data, error, isError, isFetching, isLoading, refetch } = query;
  const items = data?.items ?? [];
  const busy = items.filter((item) => item.status === "busy").length;

  return (
    <div className="page">
      <PageHeading
        eyebrow="Configuration · Resource pools"
        title="资源池"
        description="固定 Linux Runner 的在线状态、标签与最近心跳。"
        actions={
          <Button onClick={() => void refetch()} loading={isFetching} icon={<Icon name="refresh" size={14} />}>
            刷新
          </Button>
        }
      />

      <div className="banner info overview-note">
        <Icon name="server" size={15} />
        <div>
          <strong>M2 固定 Runner。</strong>
          当前使用一台固定 Linux 机器；竞价弹性资源与多 Runner 扩容在 M4 接入。
        </div>
      </div>

      {isLoading ? (
        <div className="grid grid-4">
          {[0, 1, 2, 3].map((index) => (
            <StatusCard key={index} label="" value="" icon="server" loading />
          ))}
        </div>
      ) : null}

      {isError && !data ? <QueryError error={error} onRetry={() => void refetch()} isRetrying={isFetching} /> : null}

      {data ? (
        <>
          {isError ? (
            <div className="banner warning overview-note" role="alert">
              <Icon name="alert" size={15} />
              <div className="overview-note-body">
                <span>刷新 Runner 状态失败，以下为最近一次成功数据。</span>
                <Button type="link" size="small" onClick={() => void refetch()} loading={isFetching}>
                  重试
                </Button>
              </div>
            </div>
          ) : null}

          <div className="grid grid-4">
            <StatusCard label="在线 Runner" value={data.counts.online} icon="server" tone="success" meta={<span>{data.counts.total} 台已注册</span>} />
            <StatusCard label="Runner 总数" value={data.counts.total} icon="overview" meta={<span>固定容量</span>} />
            <StatusCard label="忙碌 Runner" value={busy} icon="play" tone={busy > 0 ? "primary" : undefined} meta={<span>{busy > 0 ? "正在执行任务" : "当前空闲"}</span>} />
            <StatusCard label="心跳超时阈值" value={`${data.stale_after_seconds}s`} icon="clock" meta={<span>超时自动标记离线</span>} />
          </div>

          <section className="card section-gap">
            <div className="card-header">
              <div>
                <h2 className="card-title">Runner 列表</h2>
                <p className="card-subtitle">状态来自 Runner 心跳；页面每 15 秒刷新一次。</p>
              </div>
              <div className="card-actions">
                <span className="tag tag-neutral">{data.counts.total} 台</span>
              </div>
            </div>
            <Table<RunnerItem>
              className="recent-table"
              columns={columns}
              dataSource={items}
              rowKey="id"
              pagination={false}
              locale={{
                emptyText: (
                  <EmptyState
                    compact
                    inline
                    icon="server"
                    title="尚未注册 Runner"
                    description="在固定 Linux 主机上运行 infra/runner/install.sh 后，这里会显示在线状态与心跳。"
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

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Empty, Input, Modal, Select, Skeleton } from "antd";
import { useNavigate } from "react-router-dom";
import {
  fetchDatasetAdapters,
  fetchDatasetCatalog,
  fetchDatasetImports,
  type DatasetAdapterMetadata,
  type DatasetCatalogEntry,
  type DatasetImportRecord,
} from "../api/datasets";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { StatusCard } from "../components/StatusCard";

type CatalogStatus = "ready" | "draft" | "planned";

interface CatalogCard {
  key: string;
  datasetId: string | null;
  adapterKey: string;
  adapterAvailable: boolean;
  adapterName: string;
  name: string;
  description: string;
  taskFamily: string;
  environmentKind: string;
  versionLabel: string;
  caseCount: number | null;
  metricLabel: string;
  status: CatalogStatus;
  updatedAt: string | null;
}

const PAGE_SIZE_OPTIONS = [6, 9, 12];
const STATUS_OPTIONS = [
  { value: "all", label: "全部状态" },
  { value: "ready", label: "可评测" },
  { value: "draft", label: "待导入" },
  { value: "planned", label: "适配器预留" },
];
const THEMES = ["blue", "indigo", "cyan", "green", "violet", "orange"];
const STATUS_ORDER: Record<CatalogStatus, number> = { ready: 0, draft: 1, planned: 2 };
const STATUS_LABEL: Record<CatalogStatus, string> = {
  ready: "可评测",
  draft: "待导入",
  planned: "适配器预留",
};
const STATUS_TAG_CLASS: Record<CatalogStatus, string> = {
  ready: "tag tag-success",
  draft: "tag tag-warning",
  planned: "tag tag-neutral",
};

function monogram(name: string): string {
  const words = name
    .split(/\s+/)
    .map((word) => word.replace(/[^A-Za-z0-9]/g, ""))
    .filter((word) => /^[A-Za-z]/.test(word));
  if (words.length >= 2) {
    return words
      .slice(-2)
      .map((word) => word[0])
      .join("")
      .toUpperCase();
  }
  const ascii = name.replace(/[^A-Za-z0-9]/g, "");
  if (ascii.length >= 2) return ascii.slice(0, 2).toUpperCase();
  const trimmed = name.trim();
  return trimmed ? trimmed.slice(0, 2).toUpperCase() : "DS";
}

function shortMetricLabel(label: string): string {
  const compact = label.replace(/（[^）]*）|\([^)]*\)/g, "").trim();
  return compact || label;
}

function themeFor(seed: string): string {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) % 997;
  }
  return THEMES[hash % THEMES.length];
}

function relativeTime(value: string | null): string {
  if (!value) return "—";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return "—";
  const diff = Date.now() - time;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 30 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  return new Date(value).toLocaleDateString("zh-CN");
}

function datasetCards(
  entries: DatasetCatalogEntry[],
  adapters: DatasetAdapterMetadata[],
): CatalogCard[] {
  const adapterByKey = new Map(adapters.map((adapter) => [adapter.key, adapter]));
  const cards: CatalogCard[] = entries.map((entry) => {
    const adapter = adapterByKey.get(entry.adapter_key);
    const status: CatalogStatus = !entry.adapter.registered
      ? "planned"
      : entry.status === "ready"
        ? "ready"
        : "draft";
    const metric = shortMetricLabel(
      entry.primary_metric?.display_name ??
        (adapter?.primary_metric_key ? adapter.metric_display_names[adapter.primary_metric_key] : undefined) ??
        "—",
    );
    return {
      key: entry.id,
      datasetId: entry.id,
      adapterKey: entry.adapter_key,
      adapterAvailable: entry.adapter.registered,
      adapterName: entry.adapter.display_name || adapter?.display_name || entry.adapter_key,
      name: entry.name,
      description: entry.description || adapter?.description || "暂无描述。",
      taskFamily: entry.task_family || adapter?.task_family || "未分类",
      environmentKind: entry.environment_kind || adapter?.environment_kind || "—",
      versionLabel: entry.latest_version?.version ?? "—",
      caseCount: entry.latest_version?.case_count ?? null,
      metricLabel: metric,
      status,
      updatedAt: entry.updated_at || entry.created_at,
    };
  });

  // 已注册但还没有任何数据集的适配器 = “适配器预留”卡片；只读注册表，
  // 不写死任何数据集名称。
  for (const adapter of adapters) {
    if (adapter.dataset_count > 0) continue;
    cards.push({
      key: `adapter:${adapter.key}`,
      datasetId: null,
      adapterKey: adapter.key,
      adapterAvailable: true,
      adapterName: adapter.display_name,
      name: adapter.display_name,
      description: adapter.description || "适配器已注册，等待导入首个数据集版本。",
      taskFamily: adapter.task_family || "未分类",
      environmentKind: adapter.environment_kind || "—",
      versionLabel: "—",
      caseCount: null,
      metricLabel: adapter.primary_metric_key
        ? shortMetricLabel(
            adapter.metric_display_names[adapter.primary_metric_key] ?? adapter.primary_metric_key,
          )
        : "—",
      status: "planned",
      updatedAt: null,
    });
  }
  return cards;
}

function statusTag(status: CatalogStatus) {
  return (
    <span className={STATUS_TAG_CLASS[status]}>
      {status === "ready" ? <span className="status-dot" /> : null}
      {STATUS_LABEL[status]}
    </span>
  );
}

function importStatus(record: DatasetImportRecord): {
  label: string;
  className: string;
  icon: "check" | "alert" | "refresh";
} {
  if (record.status === "succeeded") {
    return { label: "完成", className: "tag tag-success", icon: "check" };
  }
  if (record.status === "duplicate") {
    return { label: "重复导入", className: "tag tag-neutral", icon: "refresh" };
  }
  return { label: "失败", className: "tag tag-danger", icon: "alert" };
}

interface AdapterGuideProps {
  open: boolean;
  onClose: () => void;
  adapters: DatasetAdapterMetadata[];
  loading: boolean;
  failed: boolean;
}

function AdapterGuide({ open, onClose, adapters, loading, failed }: AdapterGuideProps) {
  return (
    <Modal
      open={open}
      onCancel={onClose}
      title="适配器规范"
      width={760}
      footer={<Button onClick={onClose}>关闭</Button>}
    >
      <div className="dataset-guide">
        <p className="dataset-guide-intro">
          数据集目录由后端适配器注册表生成：任务族、环境形态、主指标与字段可见性都随适配器声明变化，
          新增数据集类型只需注册适配器，不需要改目录页。
        </p>
        {loading ? <Skeleton active paragraph={{ rows: 4 }} /> : null}
        {failed ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="适配器注册表加载失败，请关闭后重试。" />
        ) : null}
        {!loading && !failed && adapters.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有注册任何数据集适配器" />
        ) : null}
        {adapters.map((adapter) => (
          <section className="dataset-guide-card" key={adapter.key}>
            <div className="row-between">
              <div>
                <strong>{adapter.display_name}</strong>
                <div className="mono small muted">
                  {adapter.key} · v{adapter.adapter_version}
                </div>
              </div>
              <span className="tag tag-primary">{adapter.dataset_count} 个数据集</span>
            </div>
            {adapter.description ? <p className="dataset-guide-description">{adapter.description}</p> : null}
            <dl className="dataset-guide-meta">
              <div>
                <dt>任务族</dt>
                <dd>{adapter.task_family ?? "未声明"}</dd>
              </div>
              <div>
                <dt>环境形态</dt>
                <dd>{adapter.environment_kind ?? "未声明"}</dd>
              </div>
              <div>
                <dt>主指标</dt>
                <dd>
                  {adapter.primary_metric_key
                    ? adapter.metric_display_names[adapter.primary_metric_key] ?? adapter.primary_metric_key
                    : "未声明"}
                </dd>
              </div>
              <div>
                <dt>指标键</dt>
                <dd className="mono">{adapter.metric_keys.join(" · ") || "—"}</dd>
              </div>
            </dl>
            <div className="dataset-guide-visibility">
              <span className="small muted">字段可见性：</span>
              {Object.entries(adapter.case_visibility).map(([field, visibility]) => (
                <span className="tag tag-neutral" key={field}>
                  {field}: {visibility}
                </span>
              ))}
            </div>
          </section>
        ))}
      </div>
    </Modal>
  );
}

export function DatasetsPage() {
  const navigate = useNavigate();
  const catalog = useQuery({
    queryKey: ["dataset-catalog"],
    queryFn: ({ signal }) => fetchDatasetCatalog(signal),
  });
  const adapters = useQuery({
    queryKey: ["dataset-adapters"],
    queryFn: ({ signal }) => fetchDatasetAdapters(signal),
  });
  const recentImports = useQuery({
    queryKey: ["dataset-imports", "recent"],
    queryFn: ({ signal }) => fetchDatasetImports(3, signal),
  });

  const [search, setSearch] = useState("");
  const [family, setFamily] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [pageSize, setPageSize] = useState(6);
  const [page, setPage] = useState(1);
  const [guideOpen, setGuideOpen] = useState(false);

  const adapterItems = adapters.data ?? [];
  const cards = useMemo(
    () => datasetCards(catalog.data ?? [], adapterItems),
    [catalog.data, adapterItems],
  );

  const familyCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const card of cards) {
      counts.set(card.taskFamily, (counts.get(card.taskFamily) ?? 0) + 1);
    }
    return [...counts.entries()];
  }, [cards]);

  const familyOptions = useMemo(() => ["all", ...familyCounts.map(([name]) => name)], [familyCounts]);

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return cards
      .filter((card) => {
        if (family !== "all" && card.taskFamily !== family) return false;
        if (statusFilter !== "all" && card.status !== statusFilter) return false;
        if (!keyword) return true;
        return [card.name, card.adapterName, card.adapterKey, card.taskFamily, card.environmentKind]
          .join(" ")
          .toLowerCase()
          .includes(keyword);
      })
      .sort((left, right) => {
        const byStatus = STATUS_ORDER[left.status] - STATUS_ORDER[right.status];
        if (byStatus !== 0) return byStatus;
        const byCases = (right.caseCount ?? -1) - (left.caseCount ?? -1);
        return byCases !== 0 ? byCases : left.name.localeCompare(right.name, "zh-CN");
      });
  }, [cards, family, search, statusFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const pageStart = (currentPage - 1) * pageSize;
  const visible = filtered.slice(pageStart, pageStart + pageSize);

  const readyCount = cards.filter((card) => card.status === "ready").length;
  const draftCount = cards.filter((card) => card.status === "draft").length;
  const plannedCount = cards.filter((card) => card.status === "planned").length;
  const totalCases = cards.reduce((sum, card) => sum + (card.caseCount ?? 0), 0);
  const pendingCount = cards.length - readyCount;
  const loading = catalog.isLoading || adapters.isLoading;

  const datasetNames = useMemo(
    () => new Map((catalog.data ?? []).map((entry) => [entry.slug, entry.name])),
    [catalog.data],
  );

  function resetFilters() {
    setSearch("");
    setFamily("all");
    setStatusFilter("all");
    setPage(1);
  }

  function renderCardAction(card: CatalogCard) {
    if (card.status === "ready") {
      return (
        <Button size="small" onClick={() => card.datasetId && navigate(`/datasets/${card.datasetId}`)}>
          查看详情 <Icon name="arrowRight" size={13} />
        </Button>
      );
    }
    if (card.status === "draft") {
      return (
        <Button size="small" onClick={() => navigate("/datasets/import")}>
          继续导入 <Icon name="arrowRight" size={13} />
        </Button>
      );
    }
    return (
      <Button size="small" type="text" onClick={() => setGuideOpen(true)}>
        {card.adapterAvailable ? "查看适配计划" : "适配器不可用"} <Icon name="arrowRight" size={13} />
      </Button>
    );
  }

  return (
    <section className="page dataset-catalog-page">
      <PageHeading
        eyebrow="Dataset catalog"
        title="数据集"
        description="以统一卡片浏览不同任务族、适配器和指标定义；SWE-bench 只是当前首个已接入的数据集类型。"
        actions={
          <>
            <Button icon={<Icon name="boxes" size={14} />} onClick={() => setGuideOpen(true)}>
              适配器规范
            </Button>
            <Button
              type="primary"
              icon={<Icon name="plus" size={14} />}
              onClick={() => navigate("/datasets/import")}
            >
              导入数据集
            </Button>
          </>
        }
      />

      <div className="banner dataset-catalog-banner">
        <Icon name="sparkles" size={16} />
        <div>
          <strong>插件化数据集目录</strong> · 每张卡片由数据集类型注册表生成，版本、用例数量、
          核心指标与环境形态均可随适配器变化。
        </div>
      </div>

      <div className="grid grid-4 dataset-metric-grid">
        <StatusCard
          label="目录类型"
          value={cards.length}
          meta={`${familyCounts.length} 类任务族 · ${adapterItems.length} 个适配器位`}
          icon="boxes"
          tone="primary"
          loading={loading}
        />
        <StatusCard
          label="已接入版本"
          value={readyCount}
          meta="已完成字段与任务包校验"
          icon="check"
          tone="success"
          loading={loading}
        />
        <StatusCard
          label="目录用例总量"
          value={totalCases.toLocaleString("zh-CN")}
          meta="按各数据集当前版本统计"
          icon="database"
          tone="violet"
          loading={loading}
        />
        <StatusCard
          label="待接入类型"
          value={pendingCount}
          meta={`${draftCount} 待导入 · ${plannedCount} 适配器预留`}
          icon="pipeline"
          tone="warning"
          loading={loading}
        />
      </div>

      <section className="card dataset-catalog">
        <div className="dataset-catalog-head">
          <div>
            <h2 className="card-title">数据集目录</h2>
            <p className="card-subtitle">可按任务族、接入状态或关键字筛选；每页固定展示独立卡片</p>
          </div>
          <div className="row dataset-catalog-counts" aria-live="polite">
            <span className="tag tag-primary">{filtered.length} 个结果</span>
            <span className="tag tag-neutral">每页 {pageSize} 个</span>
          </div>
        </div>

        <div className="dataset-filter-row">
          <Input
            className="dataset-search"
            allowClear
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
            prefix={<Icon name="search" size={14} />}
            placeholder="搜索数据集、适配器或任务类型"
            aria-label="搜索数据集"
          />
          <div className="dataset-category-tabs" role="group" aria-label="任务族筛选">
            {familyOptions.map((name) => {
              const count =
                name === "all" ? cards.length : familyCounts.find(([entry]) => entry === name)?.[1] ?? 0;
              return (
                <button
                  type="button"
                  key={name}
                  className={`segment${family === name ? " active" : ""}`}
                  aria-pressed={family === name}
                  onClick={() => {
                    setFamily(name);
                    setPage(1);
                  }}
                >
                  {name === "all" ? "全部" : name}
                  <span className="segment-count">{count}</span>
                </button>
              );
            })}
          </div>
          <Select
            className="dataset-status-select"
            value={statusFilter}
            options={STATUS_OPTIONS}
            onChange={(value) => {
              setStatusFilter(value);
              setPage(1);
            }}
            aria-label="接入状态"
          />
        </div>

        {catalog.error ? (
          <QueryError
            error={catalog.error}
            onRetry={() => catalog.refetch()}
            isRetrying={catalog.isFetching}
            title="无法加载数据集目录"
            endpoint="GET /api/v1/datasets"
          />
        ) : null}
        {adapters.error ? (
          <QueryError
            error={adapters.error}
            onRetry={() => adapters.refetch()}
            isRetrying={adapters.isFetching}
            title="无法加载适配器注册表"
            endpoint="GET /api/v1/dataset-adapters"
          />
        ) : null}

        {loading ? (
          <div className="dataset-card-grid" aria-busy="true">
            {Array.from({ length: pageSize }, (_, index) => (
              <div className="card dataset-card" key={`skeleton-${index}`}>
                <Skeleton active paragraph={{ rows: 4 }} />
              </div>
            ))}
          </div>
        ) : visible.length > 0 ? (
          <div className="dataset-card-grid">
            {visible.map((card) => (
              <article className="card dataset-card" key={card.key}>
                <div className="dataset-head">
                  <div className={`dataset-logo dataset-theme-${themeFor(card.name)}`}>
                    {monogram(card.name)}
                  </div>
                  <div className="dataset-card-title">
                    <div className="dataset-name" title={card.name}>
                      {card.name}
                    </div>
                    <div className="small muted">{card.adapterName}</div>
                  </div>
                  <span className="dataset-status">{statusTag(card.status)}</span>
                </div>
                <p className="dataset-description">{card.description}</p>
                <div className="dataset-tags">
                  <span className="tag tag-primary">{card.taskFamily}</span>
                  <span className="tag tag-neutral">{card.environmentKind}</span>
                </div>
                <div className="dataset-meta-row">
                  <div className="dataset-meta-item">
                    <div className="dataset-meta-label">当前版本</div>
                    <div className="dataset-meta-value">{card.versionLabel}</div>
                  </div>
                  <div className="dataset-meta-item">
                    <div className="dataset-meta-label">用例数</div>
                    <div className="dataset-meta-value">
                      {card.caseCount === null ? "—" : card.caseCount.toLocaleString("zh-CN")}
                    </div>
                  </div>
                  <div className="dataset-meta-item">
                    <div className="dataset-meta-label">核心指标</div>
                    <div className="dataset-meta-value">{card.metricLabel}</div>
                  </div>
                </div>
                <div className="dataset-footer">
                  <span className="small muted">
                    {card.status === "planned" ? "仅注册 · 等待导入" : relativeTime(card.updatedAt)}
                  </span>
                  {renderCardAction(card)}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            icon="search"
            title="没有匹配的数据集"
            description="调整任务族、接入状态或搜索关键字。"
            action={
              <Button size="small" onClick={resetFilters}>
                清除筛选
              </Button>
            }
          />
        )}

        <div className="dataset-pagination">
          <div className="row small muted dataset-pagination-info">
            <span>
              显示 {filtered.length ? pageStart + 1 : 0}–
              {Math.min(pageStart + pageSize, filtered.length)} / {filtered.length}
            </span>
            <span>·</span>
            <label className="row dataset-page-size-label">
              每页
              <Select
                size="small"
                className="dataset-page-size"
                value={pageSize}
                options={PAGE_SIZE_OPTIONS.map((size) => ({ value: size, label: String(size) }))}
                onChange={(value) => {
                  setPageSize(value);
                  setPage(1);
                }}
                aria-label="每页卡片数"
              />
            </label>
          </div>
          <div className="pagination-buttons">
            <button
              type="button"
              className="page-button"
              onClick={() => setPage(currentPage - 1)}
              disabled={currentPage <= 1}
              aria-label="上一页"
            >
              <Icon name="arrowLeft" size={13} />
            </button>
            {Array.from({ length: pageCount }, (_, index) => index + 1).map((value) => (
              <button
                type="button"
                key={value}
                className={`page-button${currentPage === value ? " active" : ""}`}
                onClick={() => setPage(value)}
                aria-current={currentPage === value ? "page" : undefined}
              >
                {value}
              </button>
            ))}
            <button
              type="button"
              className="page-button"
              onClick={() => setPage(currentPage + 1)}
              disabled={currentPage >= pageCount}
              aria-label="下一页"
            >
              <Icon name="arrowRight" size={13} />
            </button>
          </div>
        </div>
      </section>

      <div className="grid dataset-bottom-grid section-gap">
        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">通用指标模型</h2>
              <p className="card-subtitle">结果页读取当前版本注册的指标定义</p>
            </div>
            <span className="tag tag-violet">插件注册</span>
          </div>
          <div className="card-body stack">
            <div className="row-between">
              <div>
                <strong>主指标</strong>
                <div className="small muted">解决率、准确率、任务成功率或加权得分</div>
              </div>
              <span className="tag tag-primary">动态渲染</span>
            </div>
            <div className="row-between">
              <div>
                <strong>质量分布</strong>
                <div className="small muted">难度、任务族、环境或失败类型</div>
              </div>
              <span className="tag tag-primary">数据集定义</span>
            </div>
            <div className="row-between">
              <div>
                <strong>成本与效率</strong>
                <div className="small muted">Token、时长、模型费和算力费</div>
              </div>
              <span className="tag tag-primary">通用指标</span>
            </div>
          </div>
        </section>

        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">最近导入</h2>
              <p className="card-subtitle">不绑定具体数据集类型</p>
            </div>
            {recentImports.isFetching ? <span className="tag tag-neutral">刷新中</span> : null}
          </div>
          <div className="card-body stack">
            {recentImports.error ? (
              <div className="small text-danger">最近导入加载失败，请稍后重试。</div>
            ) : null}
            {!recentImports.error && !recentImports.isLoading && (recentImports.data ?? []).length === 0 ? (
              <EmptyState
                compact
                inline
                title="还没有导入记录"
                description="导入第一个数据集版本后会出现在这里。"
              />
            ) : null}
            {(recentImports.data ?? []).map((record) => {
              const status = importStatus(record);
              const name = datasetNames.get(record.dataset_slug) ?? record.dataset_slug;
              return (
                <div className="row-between" key={record.id}>
                  <div className="dataset-import-row">
                    <div className={`dataset-import-icon ${status.icon === "check" ? "success" : "warning"}`}>
                      <Icon name={status.icon} size={14} />
                    </div>
                    <div>
                      <strong>
                        {name} · {record.version}
                      </strong>
                      <div className="small muted">
                        {record.valid_count} 个用例
                        {record.invalid_count > 0 ? ` · ${record.invalid_count} 个校验失败` : " · 0 个校验失败"} ·{" "}
                        {relativeTime(record.created_at)}
                      </div>
                    </div>
                  </div>
                  <span className={status.className}>{status.label}</span>
                </div>
              );
            })}
          </div>
        </section>
      </div>

      <AdapterGuide
        open={guideOpen}
        onClose={() => setGuideOpen(false)}
        adapters={adapterItems}
        loading={adapters.isLoading}
        failed={Boolean(adapters.error)}
      />
    </section>
  );
}

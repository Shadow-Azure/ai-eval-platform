import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Card,
  Collapse,
  Descriptions,
  Drawer,
  Progress,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Tooltip,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import {
  fetchDataset,
  fetchDatasetCase,
  fetchDatasetCasesPage,
  fetchDatasetVersion,
  fetchDatasetVersions,
  type DatasetCaseDetail,
  type DatasetCaseSummary,
  type DatasetDifficultyLevel,
  type DatasetImportValidation,
  type DatasetVersion,
  type MetricDefinition,
} from "../api/datasets";
import { CaseBrowser } from "../components/CaseBrowser";
import { EmptyState } from "../components/EmptyState";
import { Icon, type IconName } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";

const DIFFICULTY_LABEL: Record<DatasetDifficultyLevel, string> = {
  easy: "easy",
  medium: "medium",
  hard: "hard",
};

// Difficulty is a classification, not a health status: the prototype renders
// it as a neutral tag, and design 09 reserves success/warning/error for status.
const DIFFICULTY_TAG = "tag tag-neutral";

const IMPORT_STATUS_LABEL: Record<string, string> = {
  succeeded: "成功",
  duplicate: "重复导入",
  validated: "已校验",
  committing: "生成版本中",
  validating: "校验中",
  queued: "排队中",
  failed: "失败",
  cancelled: "已取消",
};

const versionColumns: TableColumnsType<DatasetVersion> = [
  { title: "版本", dataIndex: "version", key: "version" },
  { title: "用例数", dataIndex: "case_count", key: "case_count", width: 96 },
  { title: "来源修订", dataIndex: "source_revision", key: "source_revision", ellipsis: true },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag>{value}</Tag> },
  {
    title: "创建时间",
    dataIndex: "created_at",
    key: "created_at",
    render: (value: string) => new Date(value).toLocaleString("zh-CN"),
  },
];

const metricColumns: TableColumnsType<MetricDefinition> = [
  { title: "指标", dataIndex: "display_name", key: "display_name" },
  { title: "key", dataIndex: "metric_key", key: "metric_key", className: "mono" },
  { title: "口径", dataIndex: "formula_text", key: "formula_text", ellipsis: true },
  { title: "版本", dataIndex: "version", key: "version", width: 72 },
];

function shortMetricName(label: string): string {
  const compact = label.replace(/（[^）]*）|\([^)]*\)/g, "").trim();
  return compact || label;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString("zh-CN");
}

function baseCommitLabel(value: string | null): string {
  return value ? value.slice(0, 7) : "—";
}

const digestStyle = {
  display: "block",
  maxWidth: 264,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap" as const,
};

function DifficultyTag({ caseRow }: { caseRow: DatasetCaseSummary }) {
  if (!caseRow.difficulty) {
    return <span className="muted small">—</span>;
  }
  return (
    <Tooltip title={caseRow.difficulty_label ? `上游难度：${caseRow.difficulty_label}` : undefined}>
      <span className={DIFFICULTY_TAG}>{DIFFICULTY_LABEL[caseRow.difficulty]}</span>
    </Tooltip>
  );
}

const caseColumns: TableColumnsType<DatasetCaseSummary> = [
  { title: "#", dataIndex: "ordinal", key: "ordinal", width: 56 },
  {
    title: "实例 ID",
    dataIndex: "case_key",
    key: "case_key",
    width: 262,
    ellipsis: true,
    render: (value: string) => (
      <Tooltip title={value}>
        <span className="mono table-primary" style={{ whiteSpace: "nowrap" }}>
          {value}
        </span>
      </Tooltip>
    ),
  },
  { title: "仓库", dataIndex: "repo", key: "repo", ellipsis: true, render: (value: string | null) => value || "—" },
  {
    title: "难度",
    dataIndex: "difficulty",
    key: "difficulty",
    width: 92,
    render: (_value, record) => <DifficultyTag caseRow={record} />,
  },
  {
    title: "基准提交",
    dataIndex: "base_commit",
    key: "base_commit",
    width: 108,
    render: (value: string | null) => (
      <Tooltip title={value || undefined}>
        <span className="mono small">{baseCommitLabel(value)}</span>
      </Tooltip>
    ),
  },
  {
    title: "应通过测试",
    dataIndex: "expected_test_count",
    key: "expected_test_count",
    width: 108,
    render: (value: number) => (value > 0 ? `${value} 项` : "—"),
  },
];

function MetricCard({
  label,
  value,
  meta,
  icon,
  tone,
}: {
  label: string;
  value: string;
  meta: string;
  icon: IconName;
  tone?: "success" | "violet" | "warning";
}) {
  return (
    <div className="card metric-card">
      <div className={`metric-icon${tone ? ` ${tone}` : ""}`}>
        <Icon name={icon} size={15} />
      </div>
      <div className="metric-label">{label}</div>
      <div
        className="metric-value"
        style={value.length > 14 ? { fontSize: 20, letterSpacing: "-0.4px" } : undefined}
      >
        {value}
      </div>
      <div className="metric-meta">{meta}</div>
    </div>
  );
}

function fieldCompletenessHint(validation: DatasetImportValidation) {
  const rows = Object.entries(validation.field_completeness);
  if (!rows.length) {
    return "导入校验报告未记录逐字段完整率。";
  }
  return (
    <div>
      {rows.map(([field, entry]) => (
        <div key={field}>
          {field}: {entry.percent}%（{entry.present} 条）
        </div>
      ))}
    </div>
  );
}

function ValidationCard({
  validation,
  caseCount,
}: {
  validation: DatasetImportValidation | null;
  caseCount: number;
}) {
  const statusTag = validation ? (
    validation.passed ? (
      <span className="tag tag-success">通过</span>
    ) : (
      <span className="tag tag-danger">失败</span>
    )
  ) : (
    <span className="tag tag-neutral">未记录</span>
  );
  const percent = validation?.completeness_percent ?? null;

  return (
    <Card
      title="校验结果"
      extra={statusTag}
      className="ds-validation-card"
    >
      {!validation ? (
        <p className="small muted" style={{ margin: 0 }}>
          该版本没有可追溯的导入校验记录。
        </p>
      ) : (
        <div>
          <div className={validation.passed ? "banner success" : "banner warning"}>
            <Icon name={validation.passed ? "check" : "alert"} size={15} />
            <div>
              <strong>
                {validation.passed
                  ? `导入校验通过 · ${validation.valid_count}/${validation.total_count} 条用例有效`
                  : `导入校验未通过 · ${validation.invalid_count} 条用例有问题`}
              </strong>
              <div className="small muted" style={{ marginTop: 4 }}>
                记录于 {formatDate(validation.created_at)} · 状态{" "}
                {IMPORT_STATUS_LABEL[validation.status] ?? validation.status} · 版本内 {caseCount} 条用例
              </div>
            </div>
          </div>
          <div className="row-between small" style={{ marginTop: 14 }}>
            <Tooltip title={fieldCompletenessHint(validation)}>
              <span className="muted" style={{ cursor: "help" }}>
                字段完整率
              </span>
            </Tooltip>
            <strong>{percent === null ? "未记录" : `${percent}%`}</strong>
          </div>
          {percent === null ? null : (
            <Progress
              percent={Math.max(0, Math.min(100, percent))}
              showInfo={false}
              status={percent >= 100 ? "success" : "normal"}
              style={{ marginTop: 4 }}
            />
          )}
          {!validation.report_available ? (
            <p className="small muted" style={{ marginTop: 10, marginBottom: 0 }}>
              旧版 JSON 导入只记录校验计数，没有逐字段完整率；重新上传数据可获得完整报告。
            </p>
          ) : null}
        </div>
      )}
    </Card>
  );
}

function CaseDetailDrawer({ caseId, onClose }: { caseId: string | null; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ["dataset-case", caseId],
    queryFn: ({ signal }) => fetchDatasetCase(caseId as string, signal),
    enabled: Boolean(caseId),
  });
  const expectedGroups = useMemo(() => {
    const expected = detail.data?.expected_metrics ?? {};
    return Object.entries(expected)
      .filter((entry): entry is [string, unknown[]] => Array.isArray(entry[1]))
      .map(([key, values]) => ({ key, values: values.map((value) => String(value)) }));
  }, [detail.data]);

  return (
    <Drawer
      open={Boolean(caseId)}
      onClose={onClose}
      width={760}
      title={detail.data ? detail.data.case_key : "用例详情"}
      destroyOnClose
    >
      {detail.isLoading ? <Skeleton active paragraph={{ rows: 8 }} /> : null}
      {detail.error ? (
        <QueryError
          error={detail.error}
          onRetry={() => detail.refetch()}
          title="无法加载用例详情"
          endpoint="GET /api/v1/dataset-cases/:id"
        />
      ) : null}
      {detail.data ? <CaseDetailBody detail={detail.data} expectedGroups={expectedGroups} /> : null}
    </Drawer>
  );
}

function CaseDetailBody({
  detail,
  expectedGroups,
}: {
  detail: DatasetCaseDetail;
  expectedGroups: { key: string; values: string[] }[];
}) {
  const preStyle = {
    margin: "8px 0 0",
    padding: 12,
    border: "1px solid var(--line)",
    borderRadius: 8,
    background: "#fbfcfe",
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
    fontFamily: "var(--mono)",
    fontSize: 12,
    lineHeight: 1.6,
  };
  const patchItems = [
    { key: "reference_patch", label: "参考补丁", value: detail.reference_patch },
    { key: "test_patch", label: "测试补丁", value: detail.test_patch },
  ];
  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Descriptions size="small" column={2}>
        <Descriptions.Item label="仓库">{detail.repo || "—"}</Descriptions.Item>
        <Descriptions.Item label="基准提交">
          <span className="mono" style={{ wordBreak: "break-all" }}>
            {detail.base_commit || "—"}
          </span>
        </Descriptions.Item>
        <Descriptions.Item label="难度">
          {detail.difficulty ? (
            <span className={DIFFICULTY_TAG}>
              {DIFFICULTY_LABEL[detail.difficulty]}
              {detail.difficulty_label ? ` · ${detail.difficulty_label}` : ""}
            </span>
          ) : (
            "—"
          )}
        </Descriptions.Item>
        <Descriptions.Item label="应通过测试">{detail.expected_test_count} 项</Descriptions.Item>
      </Descriptions>
      <section>
        <h3 className="card-title">题面</h3>
        <pre style={preStyle}>{detail.problem_statement || "—"}</pre>
      </section>
      {expectedGroups.length ? (
        <section>
          <h3 className="card-title">预期检查</h3>
          <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
            {expectedGroups.map((group) => (
              <div key={group.key}>
                <div className="small muted">
                  {group.key} · {group.values.length} 项
                </div>
                <div className="mono small" style={{ wordBreak: "break-all" }}>
                  {group.values.join(" · ")}
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      <section>
        <h3 className="card-title">评分材料</h3>
        <Collapse
          size="small"
          style={{ marginTop: 8 }}
          items={patchItems.map((item) => ({
            key: item.key,
            label: (
              <span>
                {item.label} <span className="tag tag-neutral">评分专用 · Agent 不可见</span>
              </span>
            ),
            children: <pre style={preStyle}>{item.value || "（空）"}</pre>,
          }))}
        />
      </section>
      {Object.keys(detail.extra).length ? (
        <section>
          <h3 className="card-title">数据集特有字段</h3>
          <pre style={preStyle}>{JSON.stringify(detail.extra, null, 2)}</pre>
        </section>
      ) : null}
    </div>
  );
}

export function DatasetDetailPage() {
  const { datasetId = "" } = useParams();
  const navigate = useNavigate();
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [difficulty, setDifficulty] = useState<DatasetDifficultyLevel | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);

  const dataset = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: ({ signal }) => fetchDataset(datasetId, signal),
  });
  const versions = useQuery({
    queryKey: ["dataset", datasetId, "versions"],
    queryFn: ({ signal }) => fetchDatasetVersions(datasetId, signal),
  });
  const versionId = selectedVersion ?? versions.data?.[0]?.id ?? "";
  const version = useQuery({
    queryKey: ["dataset-version", versionId],
    queryFn: ({ signal }) => fetchDatasetVersion(versionId, signal),
    enabled: Boolean(versionId),
  });
  const cases = useQuery({
    queryKey: ["dataset-version", versionId, "cases", { search, difficulty, page, pageSize }],
    queryFn: ({ signal }) =>
      fetchDatasetCasesPage(
        versionId,
        {
          limit: pageSize,
          offset: (page - 1) * pageSize,
          q: search || undefined,
          difficulty: difficulty ?? undefined,
        },
        signal,
      ),
    enabled: Boolean(versionId),
    placeholderData: (previous) => previous,
  });

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchDraft.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchDraft]);

  // Facets are always computed over the whole version. If the selected level
  // disappears after a refresh, clear it instead of leaving an invisible
  // filter that returns zero rows while the control is disabled.
  useEffect(() => {
    const available = cases.data?.difficulties;
    if (!difficulty || !available) return;
    if (!available.some((facet) => facet.value === difficulty)) {
      setDifficulty(null);
      setPage(1);
    }
  }, [cases.data?.difficulties, difficulty]);

  const difficulties = cases.data?.difficulties ?? [];
  const difficultyOptions = difficulties.map((facet) => ({
    value: facet.value,
    label: `${DIFFICULTY_LABEL[facet.value]} · ${facet.count}`,
  }));
  const primaryMetric = useMemo(() => {
    const metrics = version.data?.metrics ?? [];
    return metrics.find((metric) => metric.metric_key === "resolved_rate") ?? metrics[0] ?? null;
  }, [version.data]);
  const refreshing = version.isFetching || cases.isFetching;
  const hasFilters = Boolean(search) || difficulty !== null;

  const refresh = () => {
    void dataset.refetch();
    void versions.refetch();
    void version.refetch();
    void cases.refetch();
  };

  return (
    <section className="page">
      <PageHeading
        eyebrow="Dataset version"
        title={dataset.data?.name ?? "数据集详情"}
        description={`版本内容不可变；用例、环境定义、判分命令与指标映射在导入时一并固化。适配器 ${dataset.data?.adapter_key ?? "—"} · slug ${dataset.data?.slug ?? "—"}`}
        actions={
          <Space>
            <Button icon={<Icon name="refresh" size={14} />} loading={refreshing} onClick={refresh}>
              刷新
            </Button>
            <Button
              type="primary"
              icon={<Icon name="play" size={14} />}
              disabled={!versionId}
              onClick={() => navigate(`/evals/new?datasetVersionId=${versionId}`)}
            >
              用此数据集发起评测
            </Button>
          </Space>
        }
      />
      {dataset.error ? <QueryError error={dataset.error} onRetry={() => dataset.refetch()} title="无法加载数据集" /> : null}

      <Card title="版本" style={{ marginBottom: 16 }}>
        <Table<DatasetVersion>
          rowKey="id"
          size="small"
          loading={versions.isLoading}
          columns={versionColumns}
          dataSource={versions.data ?? []}
          pagination={false}
          rowClassName={(record) => (record.id === versionId ? "ant-table-row-selected" : "")}
          onRow={(record) => ({
            onClick: () => {
              setSelectedVersion(record.id);
              // A filter from the previous version must not silently scope the
              // next one (the new version may not even provide that difficulty).
              setDifficulty(null);
              setSelectedCaseId(null);
              setPage(1);
            },
            style: { cursor: "pointer" },
          })}
          locale={{
            emptyText: (
              <EmptyState
                icon="database"
                title="尚无数据集版本"
                description="先在数据集导入页上传并校验数据文件，生成不可变版本后再浏览用例。"
                compact
              />
            ),
          }}
        />
      </Card>

      {version.error ? (
        <QueryError error={version.error} onRetry={() => version.refetch()} title="无法加载版本详情" />
      ) : null}

      {version.isLoading && !version.data ? (
        <div className="grid grid-4 section-gap">
          {[0, 1, 2, 3].map((key) => (
            <div className="card metric-card" key={key}>
              <Skeleton active title={false} paragraph={{ rows: 2 }} />
            </div>
          ))}
        </div>
      ) : (
        <div className="grid grid-4 section-gap">
          <MetricCard
            label="版本"
            value={version.data?.version ?? "—"}
            meta={`发布于 ${formatDate(version.data?.created_at)}`}
            icon="pipeline"
          />
          <MetricCard
            label="用例数量"
            value={String(version.data?.case_count ?? 0)}
            meta={version.data?.validation?.passed ? "已通过导入校验" : "导入校验未记录"}
            icon="database"
            tone="success"
          />
          <MetricCard
            label="预期指标"
            value={primaryMetric ? shortMetricName(primaryMetric.display_name) : "—"}
            meta={primaryMetric ? `${primaryMetric.metric_key} · v${primaryMetric.version}` : "版本未注册指标"}
            icon="sparkles"
            tone="violet"
          />
          <MetricCard
            label="适配器版本"
            value={version.data ? `v${version.data.adapter_version}` : "—"}
            meta={`适配器 ${dataset.data?.adapter_key ?? "—"} · 任务格式由适配器定义`}
            icon="terminal"
            tone="warning"
          />
        </div>
      )}

      <div className="grid dashboard-grid section-gap">
        <CaseBrowser<DatasetCaseSummary>
          title="用例预览"
          subtitle="按实例 ID/仓库搜索、按难度筛选；分页与筛选都在服务端执行。"
          searchValue={searchDraft}
          onSearchChange={setSearchDraft}
          searchPlaceholder="搜索实例 ID 或仓库"
          filters={
            <Select
              allowClear
              value={difficulty ?? undefined}
              placeholder={difficulties.length ? "全部难度" : "无难度数据"}
              aria-label="按难度筛选"
              disabled={!difficulties.length}
              options={difficultyOptions}
              onChange={(value) => {
                setDifficulty(value ?? null);
                setPage(1);
              }}
              style={{ width: 136 }}
              notFoundContent="该版本未提供难度字段"
            />
          }
          columns={caseColumns}
          rows={cases.data?.items ?? []}
          rowKey="id"
          total={cases.data?.total ?? 0}
          page={page}
          pageSize={pageSize}
          onPageChange={(nextPage, nextSize) => {
            if (nextSize !== pageSize) {
              setPageSize(nextSize);
              setPage(1);
              return;
            }
            setPage(nextPage);
          }}
          loading={cases.isLoading || cases.isFetching}
          error={cases.error}
          onRetry={() => cases.refetch()}
          onRow={(record) => ({
            onClick: () => setSelectedCaseId(record.id),
            tabIndex: 0,
            role: "button",
            style: { cursor: "pointer" },
            onKeyDown: (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                setSelectedCaseId(record.id);
              }
            },
          })}
          empty={
            <EmptyState
              icon={hasFilters ? "search" : "list"}
              title={hasFilters ? "当前筛选下没有用例" : "该版本暂无用例"}
              description={
                hasFilters
                  ? "调整实例 ID/仓库关键词或难度筛选后再试。"
                  : "导入完成后用例会出现在这里。"
              }
              compact
            />
          }
        />
        <aside style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <Card title="版本元数据">
            <Descriptions size="small" column={1} colon={false}>
              <Descriptions.Item label="数据源">
                <span className="mono small" style={{ wordBreak: "break-all" }}>
                  {version.data?.source_uri || "—"}
                </span>
              </Descriptions.Item>
              <Descriptions.Item label="提交号">
                <span className="mono small" style={{ wordBreak: "break-all" }}>
                  {version.data?.source_revision || "—"}
                </span>
              </Descriptions.Item>
              <Descriptions.Item label="适配器版本">{version.data?.adapter_version ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="执行镜像 digest">
                <Tooltip title={version.data?.execution_image_digest || undefined}>
                  <span className="mono small" style={digestStyle}>
                    {version.data?.execution_image_digest || "—"}
                  </span>
                </Tooltip>
              </Descriptions.Item>
              <Descriptions.Item label="评分镜像 digest">
                <Tooltip title={version.data?.scoring_image_digest || undefined}>
                  <span className="mono small" style={digestStyle}>
                    {version.data?.scoring_image_digest || "—"}
                  </span>
                </Tooltip>
              </Descriptions.Item>
            </Descriptions>
          </Card>
          <ValidationCard validation={version.data?.validation ?? null} caseCount={version.data?.case_count ?? 0} />
        </aside>
      </div>

      <Card
        title="指标口径"
        extra={<span className="tag tag-neutral">版本注册</span>}
        className="section-gap"
      >
        <Table<MetricDefinition>
          rowKey="id"
          size="small"
          columns={metricColumns}
          dataSource={version.data?.metrics ?? []}
          pagination={false}
          locale={{
            emptyText: (
              <EmptyState icon="list" title="尚未注册指标" description="适配器导入时会注册版本指标定义。" compact />
            ),
          }}
        />
      </Card>

      <Space style={{ marginTop: 16 }}>
        <Button onClick={() => navigate("/datasets")}>返回数据集</Button>
      </Space>

      <CaseDetailDrawer caseId={selectedCaseId} onClose={() => setSelectedCaseId(null)} />
    </section>
  );
}

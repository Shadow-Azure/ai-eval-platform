import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Button, Input, Select, Space, Table } from "antd";
import type { TableColumnsType } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../app/auth";
import {
  fetchEvalTemplates,
  type EvalTemplate,
  type EvalTemplateListQuery,
} from "../api/evalTemplates";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { TemplateApiModal } from "../components/TemplateApiModal";
import {
  TemplatePreflightTag,
  TemplateSelfEvolutionTag,
  TemplateStatusTag,
  formatCostRange,
  templateLastRunLabel,
} from "../components/TemplateBits";
import { TemplateActions } from "../components/TemplateActions";
import { TemplateHistoryModal } from "../components/TemplateHistoryModal";
import { TemplateLaunchModal } from "../components/TemplateLaunchModal";
import { dateTimeLabel } from "../utils/evaluationDisplay";

const templateQueries = {
  list: (query: EvalTemplateListQuery, page: number, pageSize: number) =>
    ["eval-templates", "list", query, page, pageSize] as const,
};

export function EvalTemplatesPage() {
  const navigate = useNavigate();
  const { session } = useAuth();
  const isAdmin = session?.role === "admin";
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [launchTarget, setLaunchTarget] = useState<EvalTemplate | null>(null);
  const [apiTarget, setApiTarget] = useState<EvalTemplate | null>(null);
  const [historyTarget, setHistoryTarget] = useState<EvalTemplate | null>(null);
  const hasFilters = Boolean(debouncedSearch) || statusFilter !== "all";

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setPage(1);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const listQuery = useQuery({
    queryKey: templateQueries.list(
      {
        q: debouncedSearch || undefined,
        status: statusFilter === "all" ? undefined : statusFilter,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      },
      page,
      pageSize,
    ),
    queryFn: ({ signal }) =>
      fetchEvalTemplates(
        {
          q: debouncedSearch || undefined,
          status: statusFilter === "all" ? undefined : statusFilter,
          limit: pageSize,
          offset: (page - 1) * pageSize,
        },
        signal,
      ),
    placeholderData: keepPreviousData,
  });

  const total = listQuery.data?.total;
  useEffect(() => {
    if (total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / pageSize));
    if (page > lastPage) setPage(lastPage);
  }, [page, pageSize, total]);

  const resetFilters = () => {
    setSearch("");
    setDebouncedSearch("");
    setStatusFilter("all");
    setPage(1);
  };

  const columns: TableColumnsType<EvalTemplate> = [
    {
      title: "模板",
      key: "name",
      width: "18%",
      className: "template-name-cell",
      render: (_value, row) => (
        <div className="template-name-cell-inner">
          <Button
            type="link"
            size="small"
            className="template-name-link"
            title={row.name}
            onClick={() => navigate(`/evals/templates/${row.id}`)}
          >
            {row.name}
          </Button>
          <div className="table-secondary template-name-desc" title={row.description ?? undefined}>
            {row.description || "—"}
          </div>
        </div>
      ),
    },
    {
      title: "固定组合",
      key: "combo",
      width: "21%",
      className: "template-combo-cell",
      render: (_value, row) => {
        const datasetLine = `${row.dataset_name ?? "—"} · ${row.dataset_version ?? "—"} · ${row.case_count} 用例`;
        const agentLine = `${row.runtime_key} · ${row.model_provider ?? "—"} ${row.model_name ?? "—"}`;
        const poolLine = `${row.resource_pool_name ?? "—"} · ${row.asset_source_name ?? "无资产源"}`;
        return (
          <div className="template-combo">
            <div className="table-secondary template-combo-line" title={datasetLine}>
              <strong>{row.dataset_name ?? "—"}</strong>
              <span>
                {" "}
                · {row.dataset_version ?? "—"} · {row.case_count} 用例
              </span>
            </div>
            <div className="table-secondary template-combo-line" title={agentLine}>
              {agentLine}
            </div>
            <div className="table-secondary template-combo-line" title={poolLine}>
              {poolLine}
            </div>
          </div>
        );
      },
    },
    {
      title: "状态",
      key: "enabled",
      width: "8%",
      render: (_value, row) => (
        <>
          <TemplateStatusTag enabled={row.enabled} />
          <TemplateSelfEvolutionTag template={row} />
          <TemplatePreflightTag template={row} />
        </>
      ),
    },
    {
      title: "最近启动",
      key: "last_launched",
      width: "10%",
      render: (_value, row) => (
        <div>
          <div>{templateLastRunLabel(row)}</div>
          <div className="table-secondary">{dateTimeLabel(row.last_launched_at)}</div>
        </div>
      ),
    },
    {
      title: "累计启动",
      key: "launch_count",
      width: "7%",
      render: (_value, row) => `${row.launch_count} 次`,
    },
    {
      title: "费用估算",
      key: "cost",
      width: "7%",
      render: (_value, row) => (
        <div>
          <div>{formatCostRange(row.estimated_cost?.total_cny)}</div>
          <div className="table-secondary">
            {row.estimated_cost ? "启动时重新计算" : "未填写定价"}
          </div>
        </div>
      ),
    },
    {
      title: "更新",
      key: "updated_at",
      width: "7%",
      render: (_value, row) => (
        <div className="small muted">{dateTimeLabel(row.updated_at)}</div>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: "22%",
      render: (_value, row) => (
        <Space size={4} wrap>
          <Button size="small" onClick={() => navigate(`/evals/templates/${row.id}`)}>
            详情
          </Button>
          <Button size="small" onClick={() => setApiTarget(row)}>
            接口
          </Button>
          <Button size="small" onClick={() => setHistoryTarget(row)}>
            历史
          </Button>
          <TemplateActions
            template={row}
            canManage={isAdmin}
            canLaunch={isAdmin}
            allowDisable
            onLaunch={() => setLaunchTarget(row)}
          />
        </Space>
      ),
    },
  ];

  return (
    <div className="page">
      <PageHeading
        eyebrow="Evaluation · Templates"
        title="评测模板"
        description="把数据集版本、Agent 运行时与资产、模型凭据与资源池固化为可复用模板；列表在服务端搜索、筛选与分页。"
        actions={
          <>
            <Button icon={<Icon name="refresh" size={14} />} onClick={() => listQuery.refetch()}>
              刷新
            </Button>
            <Button
              type="primary"
              icon={<Icon name="plus" size={14} />}
              onClick={() => navigate("/evals/new?mode=template")}
            >
              新建模板
            </Button>
          </>
        }
      />

      {listQuery.isError && !listQuery.data ? (
        <QueryError
          error={listQuery.error}
          onRetry={() => listQuery.refetch()}
          title="无法加载评测模板"
          endpoint="GET /api/v1/eval-templates"
        />
      ) : (
        <section className="card template-list-card">
          <div className="template-toolbar">
            <Input
              allowClear
              aria-label="搜索评测模板"
              placeholder="搜索模板、数据集、Agent 或模型"
              prefix={<Icon name="search" size={14} />}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              style={{ width: 340 }}
            />
            <Select
              aria-label="启用状态筛选"
              value={statusFilter}
              options={[
                { value: "all", label: "状态：全部" },
                { value: "enabled", label: "状态：启用" },
                { value: "disabled", label: "状态：停用" },
              ]}
              onChange={(value) => {
                setStatusFilter(value);
                setPage(1);
              }}
              style={{ width: 150 }}
            />
            {hasFilters ? (
              <Button size="small" onClick={resetFilters}>
                清除筛选
              </Button>
            ) : null}
            <span className="small muted" style={{ marginLeft: "auto" }}>
              共 {total ?? 0} 个模板 · 服务端分页
            </span>
          </div>
          <Table
            rowKey="id"
            size="small"
            tableLayout="fixed"
            scroll={{ x: 1220 }}
            loading={listQuery.isLoading}
            columns={columns}
            dataSource={listQuery.data?.items ?? []}
            pagination={{
              current: page,
              pageSize,
              total: total ?? 0,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50],
              showTotal: (value) => `共 ${value} 个模板`,
              onChange: (nextPage, nextSize) => {
                setPage(nextSize === pageSize ? nextPage : 1);
                setPageSize(nextSize);
              },
            }}
            locale={{
              emptyText: hasFilters ? (
                <EmptyState
                  icon="search"
                  title="没有匹配的评测模板"
                  description="调整搜索词或状态筛选，或清空条件查看全部模板。"
                  action={
                    <Button size="small" onClick={resetFilters}>
                      清除筛选
                    </Button>
                  }
                />
              ) : (
                <EmptyState
                  icon="boxes"
                  title="还没有评测模板"
                  description="从发起评测向导第 4 步保存，或新建模板只保存配置、不立即启动。"
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
              ),
            }}
          />
        </section>
      )}

      <TemplateLaunchModal
        template={launchTarget}
        open={Boolean(launchTarget)}
        onClose={() => setLaunchTarget(null)}
      />
      <TemplateApiModal
        template={apiTarget}
        open={Boolean(apiTarget)}
        onClose={() => setApiTarget(null)}
        onLaunch={() => {
          if (!apiTarget) return;
          setApiTarget(null);
          setLaunchTarget(apiTarget);
        }}
      />
      <TemplateHistoryModal
        template={historyTarget}
        open={Boolean(historyTarget)}
        onClose={() => setHistoryTarget(null)}
      />
    </div>
  );
}

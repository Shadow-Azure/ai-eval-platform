import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { Alert, Button, Modal, Pagination, Select, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import type { EvalTemplate } from "../api/evalTemplates";
import {
  fetchEvaluations,
  type EvaluationListResponse,
  type EvaluationSummary,
} from "../api/evaluations";
import { EmptyState } from "./EmptyState";
import { TemplateStatusTag, caseSelectionLabel } from "./TemplateBits";
import { dateTimeLabel, durationCompact, rateLabel } from "../utils/evaluationDisplay";
import { EVAL_STATUS_META, evalStatusMeta } from "../utils/evalStatus";

const HISTORY_STATUSES = Object.keys(EVAL_STATUS_META);
const PAGE_SIZE_OPTIONS = [10, 20, 50];

interface TemplateHistoryModalProps {
  template: EvalTemplate | null;
  open: boolean;
  onClose: () => void;
}

async function fetchStatusCounts(
  templateId: string,
  signal: AbortSignal,
): Promise<Record<string, number>> {
  const results = await Promise.all(
    HISTORY_STATUSES.map((status) =>
      fetchEvaluations({ templateId, status, limit: 1 }, signal),
    ),
  );
  return Object.fromEntries(
    HISTORY_STATUSES.map((status, index) => [status, results[index].total]),
  );
}

function HistoryTable({
  query,
  emptyText,
  onOpen,
}: {
  query: UseQueryResult<EvaluationListResponse>;
  emptyText: ReactNode;
  onOpen: (runId: string) => void;
}) {
  const columns: TableColumnsType<EvaluationSummary> = useMemo(
    () => [
      {
        title: "运行",
        key: "run",
        width: "24%",
        render: (_value, row) => (
          <div className="template-history-run">
            <span className="mono" title={row.id}>
              {row.id.slice(0, 12)}
            </span>
            <Tag className="template-history-version" title="模板版本">
              v{row.template_version ?? 1}
            </Tag>
          </div>
        ),
      },
      {
        title: "状态",
        key: "status",
        width: "12%",
        render: (_value, row) => {
          const meta = evalStatusMeta(row.status);
          return <Tag color={meta.color}>{meta.label}</Tag>;
        },
      },
      {
        title: "结果",
        key: "result",
        width: "18%",
        render: (_value, row) => (
          <div className="template-history-result">
            <strong>
              {row.resolved_count ?? 0} / {row.scored_count ?? 0}
            </strong>
            <span className="table-secondary"> · {rateLabel(row.resolve_rate)}</span>
          </div>
        ),
      },
      {
        title: "启动时间",
        key: "started_at",
        width: "18%",
        render: (_value, row) => dateTimeLabel(row.started_at ?? row.created_at),
      },
      {
        title: "耗时",
        key: "duration",
        width: "12%",
        render: (_value, row) => durationCompact(row.duration_seconds),
      },
      {
        title: "",
        key: "open",
        width: "10%",
        render: (_value, row) => (
          <Button
            size="small"
            onClick={(event) => {
              event.stopPropagation();
              onOpen(row.id);
            }}
          >
            查看
          </Button>
        ),
      },
    ],
    [onOpen],
  );

  if (query.isError && !query.data) {
    return (
      <Alert
        type="error"
        showIcon
        message="无法加载启动历史"
        description={query.error instanceof Error ? query.error.message : "请稍后重试。"}
        action={
          <Button size="small" onClick={() => query.refetch()}>
            重试
          </Button>
        }
      />
    );
  }

  return (
    <Table
      rowKey="id"
      size="small"
      tableLayout="fixed"
      loading={query.isLoading}
      columns={columns}
      dataSource={query.data?.items ?? []}
      pagination={false}
      onRow={(row) => ({
        className: "template-history-row",
        onClick: () => onOpen(row.id),
      })}
      locale={{ emptyText }}
    />
  );
}

export function TemplateHistoryModal({ template, open, onClose }: TemplateHistoryModalProps) {
  const navigate = useNavigate();
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  useEffect(() => {
    if (!open) return;
    setStatus("all");
    setPage(1);
    setPageSize(10);
  }, [open, template?.id]);

  const historyQuery = useQuery({
    queryKey: ["eval-templates", template?.id, "history", status, page, pageSize],
    queryFn: ({ signal }) =>
      fetchEvaluations(
        {
          templateId: template?.id,
          status: status === "all" ? undefined : status,
          limit: pageSize,
          offset: (page - 1) * pageSize,
        },
        signal,
      ),
    enabled: open && Boolean(template?.id),
    placeholderData: keepPreviousData,
  });

  const countsQuery = useQuery({
    queryKey: ["eval-templates", template?.id, "history-counts"],
    queryFn: ({ signal }) => fetchStatusCounts(template?.id ?? "", signal),
    enabled: open && Boolean(template?.id),
  });

  const total = historyQuery.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);

  useEffect(() => {
    if (!open || !historyQuery.data) return;
    if (page > pageCount) setPage(pageCount);
  }, [open, page, pageCount, historyQuery.data]);

  const counts = countsQuery.data;
  const allCount = counts ? Object.values(counts).reduce((sum, value) => sum + value, 0) : null;

  const statusOptions = useMemo(
    () => [
      { value: "all", label: `全部状态 (${allCount ?? "…"})` },
      ...HISTORY_STATUSES.map((value) => ({
        value,
        label: `${EVAL_STATUS_META[value].label} (${counts?.[value] ?? "…"})`,
      })),
    ],
    [allCount, counts],
  );

  if (!template) return null;

  const hasFilter = status !== "all";
  const clearFilter = () => {
    setStatus("all");
    setPage(1);
  };
  const openRun = (runId: string) => {
    onClose();
    navigate(`/evals/${runId}?fromTemplate=${template.id}`);
  };
  const emptyText = hasFilter ? (
    <EmptyState
      icon="search"
      title="没有符合状态的启动记录"
      description="清除状态筛选查看全部历史。"
      action={
        <Button size="small" onClick={clearFilter}>
          清除筛选
        </Button>
      }
    />
  ) : (
    <EmptyState
      icon="clock"
      title="还没有启动记录"
      description="从该模板一键启动后，运行会在这里按时间倒序出现。"
    />
  );

  return (
    <Modal
      open={open}
      width={1000}
      className="template-history-modal"
      style={{ top: 24 }}
      title={
        <div>
          <div>启动历史 · {template.name}</div>
          <div className="template-history-heading-subtitle">
            {template.dataset_name ?? "—"} {template.dataset_version ?? ""} ·{" "}
            {template.runtime_key} · {template.model_display_name ?? template.model_name ?? "—"} · 共{" "}
            {template.launch_count} 次启动
          </div>
        </div>
      }
      onCancel={onClose}
      footer={
        <Button key="close" onClick={onClose}>
          关闭
        </Button>
      }
    >
      <div className="template-history-summary">
        <div className="template-history-summary-main">
          <span className="template-history-kicker">固定组合</span>
          <strong>
            {template.dataset_name ?? "—"} {template.dataset_version ?? ""} · {template.runtime_key} ·{" "}
            {template.model_display_name ?? template.model_name ?? "—"}
          </strong>
          <span className="small muted">
            {template.resource_pool_name ?? "—"} · {caseSelectionLabel(template)} · 每次启动创建独立评测
          </span>
        </div>
        <div className="template-history-summary-side">
          <TemplateStatusTag enabled={template.enabled} />
          <span className="small muted">
            最近一次 {dateTimeLabel(template.last_launched_at)}
          </span>
        </div>
      </div>

      <div className="template-history-panel">
        <div className="template-history-toolbar">
          <Select
            aria-label="按状态筛选启动历史"
            value={status}
            options={statusOptions}
            onChange={(value) => {
              setStatus(value);
              setPage(1);
            }}
            style={{ width: 170 }}
          />
          <span className="small muted">匹配 {total} 条 · 服务端分页</span>
          <label className="small muted template-history-page-size">
            每页
            <Select
              aria-label="启动历史每页条数"
              value={pageSize}
              options={PAGE_SIZE_OPTIONS.map((size) => ({ value: size, label: String(size) }))}
              onChange={(value) => {
                setPageSize(value);
                setPage(1);
              }}
              style={{ width: 76 }}
            />
          </label>
          {hasFilter ? (
            <Button size="small" onClick={clearFilter}>
              清除筛选
            </Button>
          ) : null}
        </div>

        {historyQuery.isError && !historyQuery.data ? (
          <Alert
            type="error"
            showIcon
            message="无法加载启动历史"
            description={
              historyQuery.error instanceof Error ? historyQuery.error.message : "请稍后重试。"
            }
            action={
              <Button size="small" onClick={() => historyQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : (
          <HistoryTable
            query={historyQuery}
            emptyText={emptyText}
            onOpen={openRun}
          />
        )}

        <div className="template-history-pagination">
          <span className="small muted">
            显示 {start}–{end} / {total} · 共 {pageCount} 页
          </span>
          <Pagination
            size="small"
            current={page}
            pageSize={pageSize}
            total={total}
            showSizeChanger={false}
            showLessItems
            onChange={(nextPage) => setPage(nextPage)}
          />
        </div>
      </div>

      <div className="template-history-api small muted">
        <span className="mono">
          GET /api/v1/evaluations?template_id={template.id}&amp;status=&amp;limit=&amp;offset=
        </span>
        <span>按启动时间倒序；点击任意一行进入评测详情</span>
      </div>
    </Modal>
  );
}

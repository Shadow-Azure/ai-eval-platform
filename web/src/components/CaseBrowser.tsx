import type { ReactNode } from "react";
import { Card, Input, Table } from "antd";
import type { TableColumnsType, TableProps } from "antd";
import { Icon } from "./Icon";
import { QueryError } from "./QueryError";
import "../styles/case-browser.css";

interface CaseBrowserProps<Row extends object> {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Search is controlled by the page so it can debounce and reset the page. */
  searchValue: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder: string;
  /** Optional first toolbar row, e.g. a status segmented control. */
  leading?: ReactNode;
  /** Additional filters rendered next to the search input. */
  filters?: ReactNode;
  columns: TableColumnsType<Row>;
  rows: Row[];
  rowKey: TableProps<Row>["rowKey"];
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number, pageSize: number) => void;
  loading: boolean;
  error?: unknown;
  onRetry: () => void;
  onRow?: TableProps<Row>["onRow"];
  empty: ReactNode;
  className?: string;
  tableClassName?: string;
  skeletonRows?: number;
}

/**
 * Shared case browser for dataset versions and eval runs (M5-14 / M5-17).
 *
 * It owns the part that must not drift between the two pages: the toolbar,
 * server-pagination controls with 20/50/100 page sizes, the initial skeleton,
 * the empty/error states and the row range label. Filtering and data fetching
 * stay with the page because their query parameters differ.
 */
export function CaseBrowser<Row extends object>({
  title,
  subtitle,
  actions,
  searchValue,
  onSearchChange,
  searchPlaceholder,
  leading,
  filters,
  columns,
  rows,
  rowKey,
  total,
  page,
  pageSize,
  onPageChange,
  loading,
  error,
  onRetry,
  onRow,
  empty,
  className,
  tableClassName,
  skeletonRows = 6,
}: CaseBrowserProps<Row>) {
  const offset = (page - 1) * pageSize;
  const showingSkeleton = loading && rows.length === 0;
  const rangeLabel = error
    ? "用例加载失败"
    : total
      ? `显示 ${offset + 1}–${Math.min(offset + rows.length, total)} / ${total}`
      : loading
        ? "正在加载最新用例…"
        : "没有匹配的用例";

  return (
    <Card
      className={className ? `case-browser-card ${className}` : "case-browser-card"}
      styles={{ body: { padding: 0 } }}
    >
      <div className="card-header case-browser-head">
        <div>
          <h2 className="card-title">{title}</h2>
          {subtitle ? <p className="card-subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="card-actions">{actions}</div> : null}
      </div>
      {leading ? <div className="case-browser-leading">{leading}</div> : null}
      <div className="case-browser-toolbar">
        <div className="case-browser-controls">
          <Input
            allowClear
            value={searchValue}
            onChange={(event) => onSearchChange(event.target.value)}
            prefix={<Icon name="search" size={14} />}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            style={{ width: 240 }}
          />
          {filters}
        </div>
        <span className="case-browser-range small muted" aria-live="polite">
          {rangeLabel}
        </span>
      </div>
      <div className="case-browser-body">
        {showingSkeleton ? (
          <div className="case-browser-skeleton" role="status" aria-label="正在加载用例">
            {Array.from({ length: skeletonRows }).map((_, index) => (
              <div className="case-browser-skeleton-row" key={index}>
                <span className="skeleton" style={{ width: `${58 - index * 4}%` }} />
                <span className="skeleton" style={{ width: "76%" }} />
                <span className="skeleton" style={{ width: "46%" }} />
                <span className="skeleton" style={{ width: "62%" }} />
              </div>
            ))}
            <span className="small muted">正在加载最新用例…</span>
          </div>
        ) : error ? (
          <div className="case-browser-error">
            <QueryError
              error={error}
              onRetry={onRetry}
              isRetrying={loading}
              title="无法加载用例"
              endpoint="GET /api/v1/dataset-versions/:id/cases"
            />
          </div>
        ) : (
          <Table<Row>
            className={tableClassName}
            rowKey={rowKey}
            size="small"
            columns={columns}
            dataSource={rows}
            loading={loading}
            onRow={onRow}
            pagination={
              total > 0
                ? {
                    current: page,
                    pageSize,
                    total,
                    showSizeChanger: true,
                    pageSizeOptions: [20, 50, 100],
                    showTotal: (value) => `共 ${value} 个用例`,
                    onChange: onPageChange,
                  }
                : false
            }
            locale={{ emptyText: empty }}
          />
        )}
      </div>
    </Card>
  );
}

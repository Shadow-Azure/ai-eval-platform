import { Button } from "antd";
import { ApiError } from "../api/types";
import { Icon } from "./Icon";

interface QueryErrorProps {
  error: unknown;
  onRetry: () => void;
  isRetrying?: boolean;
  title?: string;
  endpoint?: string;
}

function describeError(error: unknown): { message: string; detail: string[] } {
  if (error instanceof ApiError) {
    const detail: string[] = [];
    if (error.status !== null) detail.push(`HTTP ${error.status}`);
    if (error.code) detail.push(error.code);
    if (error.requestId) detail.push(`request_id ${error.requestId}`);
    return {
      message: error.message,
      detail,
    };
  }

  if (error instanceof Error && error.message) {
    return { message: error.message, detail: [] };
  }

  return { message: "请求控制面时发生未知错误。", detail: [] };
}

export function QueryError({
  error,
  onRetry,
  isRetrying = false,
  title = "无法加载总览",
  endpoint = "GET /api/v1/overview",
}: QueryErrorProps) {
  const { message, detail } = describeError(error);

  return (
    <section className="card query-error" role="alert" aria-live="assertive">
      <div className="query-error-icon" aria-hidden="true">
        <Icon name="alert" size={20} />
      </div>
      <div className="query-error-copy">
        <h2>{title}</h2>
        <p>{message}</p>
        <div className="query-error-detail">
          <span className="mono">{endpoint}</span>
          {detail.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
        <p className="query-error-hint">
          确认控制面服务已启动，且 <span className="mono">/api</span> 代理可访问后重试。
        </p>
      </div>
      <Button
        onClick={onRetry}
        loading={isRetrying}
        icon={<Icon name="refresh" size={14} />}
      >
        重试
      </Button>
    </section>
  );
}

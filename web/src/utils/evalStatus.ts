// M5-16: the run-status enum lives in the backend (`EVAL_RUN_STATUSES`) and
// is documented by the shared status ADR. The frontend only renders labels for
// enum members; it never derives a new status from case counts.

export interface EvalStatusMeta {
  label: string;
  /** antd Tag color. */
  color: string;
  /** Prototype tag tone used by the overview list. */
  tone: string;
}

export const EVAL_STATUS_META: Record<string, EvalStatusMeta> = {
  preparing: { label: "准备中", color: "default", tone: "neutral" },
  queued: { label: "排队中", color: "default", tone: "neutral" },
  running: { label: "运行中", color: "processing", tone: "primary" },
  finalizing: { label: "收口中", color: "processing", tone: "primary" },
  completed: { label: "已完成", color: "success", tone: "success" },
  partial: { label: "部分成功", color: "warning", tone: "warning" },
  failed: { label: "失败", color: "error", tone: "danger" },
  pausing_budget: { label: "预算暂停中", color: "warning", tone: "warning" },
  paused_budget: { label: "预算已暂停", color: "warning", tone: "warning" },
  cancelling: { label: "取消中", color: "warning", tone: "warning" },
  cancelled: { label: "已取消", color: "default", tone: "neutral" },
};

export const ACTIVE_EVAL_STATUSES: readonly string[] = [
  "preparing",
  "queued",
  "running",
  "finalizing",
  "pausing_budget",
  "paused_budget",
  "cancelling",
];

export const TERMINAL_EVAL_STATUSES: readonly string[] = [
  "completed",
  "partial",
  "failed",
  "cancelled",
];

export const EVAL_STATUS_OPTIONS = Object.entries(EVAL_STATUS_META).map(
  ([value, meta]) => ({ value, label: meta.label }),
);

export function evalStatusMeta(status: string): EvalStatusMeta {
  return EVAL_STATUS_META[status] ?? { label: status, color: "default", tone: "neutral" };
}

export function isTerminalEvalStatus(status: string): boolean {
  return TERMINAL_EVAL_STATUSES.includes(status);
}

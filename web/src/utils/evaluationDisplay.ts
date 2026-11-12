import type { EvaluationSummary } from "../api/evaluations";

const RUNTIME_LABELS: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex CLI",
  opencode: "OpenCode",
  pi: "pi",
};

export function runtimeLabel(key: string | null | undefined): string {
  if (!key) return "—";
  return RUNTIME_LABELS[key] ?? key;
}

export function dateTimeLabel(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const now = new Date();
  const time = parsed.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const sameDay =
    parsed.getFullYear() === now.getFullYear() &&
    parsed.getMonth() === now.getMonth() &&
    parsed.getDate() === now.getDate();
  if (sameDay) return `今天 ${time}`;
  return `${parsed.getMonth() + 1} 月 ${parsed.getDate()} 日 ${time}`;
}

export function durationLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return "—";
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分`;
  return `${Math.max(1, Math.round(seconds))} 秒`;
}

export function durationCompact(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return "—";
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${Math.max(1, Math.round(seconds))}s`;
}

export function progressPercent(row: EvaluationSummary): number {
  if (!row.total_cases) return 0;
  const finished = row.finished_count ?? Math.max(0, row.total_cases - row.queued_count - row.running_count);
  return Math.min(100, Math.round((finished / row.total_cases) * 100));
}

export function rateLabel(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)}%`;
}

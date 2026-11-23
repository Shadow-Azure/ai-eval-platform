import { Tag } from "antd";
import type { EvalTemplate } from "../api/evalTemplates";
import { durationLabel, rateLabel } from "../utils/evaluationDisplay";
import { evalStatusMeta } from "../utils/evalStatus";
import type { CostEstimate } from "../api/evaluations";

export function TemplateStatusTag({ enabled }: { enabled: boolean }) {
  return enabled ? <Tag color="success">启用</Tag> : <Tag>已停用</Tag>;
}

/** M8 #382: the list badge for a template that reports bad cases at terminal. */
export function TemplateSelfEvolutionTag({ template }: { template: EvalTemplate }) {
  if (!template.self_evolution.enabled) return null;
  return (
    <Tag color="purple" title="自进化上报已启用">
      上报
    </Tag>
  );
}

/** "已启用 · host · 工作区 ws" or "未启用"; never includes the token. */
export function selfEvolutionLabel(template: EvalTemplate): string {
  const config = template.self_evolution;
  if (!config.enabled) return "未启用";
  const host = (config.base_url ?? "").replace(/^https?:\/\//, "").replace(/\/+$/, "");
  return `已启用 · ${host || "未填地址"} · 工作区 ${config.workspace_id || "未填"}`;
}

/** A template only exposes whether a token is stored, never the token itself. */
export function selfEvolutionTokenLabel(template: EvalTemplate): string {
  return template.self_evolution.has_token ? "令牌已配置" : "未配置";
}

export function templateComboText(template: EvalTemplate): string {
  return [
    template.dataset_name ?? "未绑定数据集",
    template.runtime_key,
    template.model_display_name ?? template.model_name ?? "模型未配置",
    template.resource_pool_name ?? "资源池未配置",
  ].join(" · ");
}

export function caseSelectionLabel(template: EvalTemplate): string {
  const selection = template.case_selection;
  if (selection.mode === "count") return `前 ${selection.count ?? 0} 个用例`;
  if (selection.mode === "cases") return `${selection.case_keys?.length ?? 0} 个指定用例`;
  return `全部 ${template.case_count || template.dataset_case_count} 个用例`;
}

export function retryLabel(template: EvalTemplate): string {
  const attempts = template.execution.max_attempts ?? 1;
  return attempts > 1
    ? `开启 · 每用例最多 ${attempts} 次 attempt`
    : "关闭 · 每用例 1 次 attempt";
}

export function formatCostRange(range: { low: string; high: string } | null | undefined): string {
  if (!range) return "—";
  const low = Number(range.low);
  const high = Number(range.high);
  if (!Number.isFinite(low) || !Number.isFinite(high)) return "—";
  return `¥${low.toFixed(2)}–${high.toFixed(2)}`;
}

export function costEstimateLabel(estimate: CostEstimate | null): string {
  if (!estimate) return "该模型未填写定价，以网关实际记账为准";
  const range = estimate.total_cny;
  const low = Number(range.low);
  const high = Number(range.high);
  if (!Number.isFinite(low) || !Number.isFinite(high)) return "以网关实际记账为准";
  return `¥${low.toFixed(2)}–${high.toFixed(2)} · 预计 ${durationLabel(
    estimate.estimated_duration_seconds.high,
  )}`;
}

export function recentRunLabel(template: EvalTemplate): string {
  const run = template.recent_runs?.[0];
  if (!run) return "尚未启动";
  const meta = evalStatusMeta(run.status);
  return `${meta.label} · ${run.resolve_rate == null ? "结果聚合中" : rateLabel(run.resolve_rate)}`;
}

/** Latest state label; a template launched before run provenance existed still
 * shows "已启动" instead of "尚未启动". */
export function templateLastRunLabel(template: EvalTemplate): string {
  return template.recent_runs?.[0] || template.last_launched_at
    ? template.recent_runs?.[0]
      ? recentRunLabel(template)
      : "已启动"
    : "尚未启动";
}

/** Prototype `templateLastRunTag`: the latest run as a tag, or a neutral tag
 * before the first launch. */
export function TemplateLastRunTag({ template }: { template: EvalTemplate }) {
  const run = template.recent_runs?.[0];
  if (!run) {
    return <Tag>{template.last_launched_at ? "已启动" : "尚未启动"}</Tag>;
  }
  const meta = evalStatusMeta(run.status);
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

export function executionSummary(template: EvalTemplate): string {
  const execution = template.execution;
  const parts = [`并发 ${execution.concurrency}`];
  if (execution.case_timeout_seconds) {
    parts.push(`超时 ${durationLabel(execution.case_timeout_seconds)}`);
  }
  if (execution.model_budget_cny != null) {
    parts.push(`预算 ¥${Number(execution.model_budget_cny).toFixed(2)}`);
  }
  return parts.join(" · ");
}

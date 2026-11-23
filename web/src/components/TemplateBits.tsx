import { Alert, Tag } from "antd";
import type { EvalTemplate, EvalTemplatePreflightBlocker } from "../api/evalTemplates";
import { Icon } from "./Icon";
import { durationLabel, rateLabel } from "../utils/evaluationDisplay";
import { evalStatusMeta } from "../utils/evalStatus";
import type { CostEstimate, EvaluationTemplateBrief } from "../api/evaluations";

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

/** #398: map a launch blocker code to the operator's concrete repair action. */
export function blockerFixHint(code: string): string {
  switch (code) {
    case "scoring_image_missing":
    case "execution_image_missing":
      return "数据集版本未记录镜像 digest，且平台未配置默认值；请在数据集版本补齐 digest，或联系管理员配置平台默认镜像。";
    case "asset_runtime_setup_required":
      return "资产冻结时加载测试未通过；请到 Agent 资产页重新执行加载测试并重新解析快照。";
    case "credential_unavailable":
      return "模型凭据不存在或已撤销；请先在「模型凭据」页替换 Key。";
    case "capacity_invalid":
      return "请求并发超过资源池上限；请降低并发或调整资源池容量。";
    case "runner_labels_mismatch":
      return "资源池 Runner 标签不满足 Runtime 要求；请检查并修正 Runner 标签。";
    default:
      return "按上面的原因修正配置后重试。";
  }
}

/** Short field label for a blocker, tolerating both launch and save paths. */
export function blockerFieldLabel(field: string | undefined): string {
  if (!field) return "—";
  if (field.endsWith("execution_image_digest")) return "执行镜像 digest";
  if (field.endsWith("scoring_image_digest")) return "评分镜像 digest";
  return field;
}

export function templatePreflightBlockers(template: EvalTemplate): EvalTemplatePreflightBlocker[] {
  return template.preflight?.blockers ?? [];
}

/** #398: list/detail badge for an existing template blocked before launch. */
export function TemplatePreflightTag({ template }: { template: EvalTemplate }) {
  const blockers = templatePreflightBlockers(template);
  if (!blockers.length) return null;
  return (
    <Tag color="error" title={`启动会被阻断：${blockers.map((blocker) => blocker.message).join("、")}`}>
      待修复
    </Tag>
  );
}

/** Reusable blocker explanation: code, cause, field and repair action. */
export function TemplateBlockerList({
  blockers,
  title,
  description,
}: {
  blockers: EvalTemplatePreflightBlocker[];
  title?: string;
  description?: string;
}) {
  if (!blockers.length) return null;
  return (
    <Alert
      type="error"
      showIcon
      message={title ?? `发现 ${blockers.length} 个阻断项`}
      description={
        <div className="template-blocker-list">
          {description ? <p style={{ marginTop: 0 }}>{description}</p> : null}
          <ul>
            {blockers.map((blocker, index) => (
              <li key={`${blocker.code}-${index}`}>
                <div className="template-blocker-head">
                  <strong>{blocker.message}</strong>
                  <Tag color="error">{blocker.code}</Tag>
                  {blocker.field ? (
                    <span className="small muted">字段 {blockerFieldLabel(blocker.field)}</span>
                  ) : null}
                </div>
                <div className="small">修复建议：{blockerFixHint(blocker.code)}</div>
              </li>
            ))}
          </ul>
        </div>
      }
    />
  );
}

/** "执行 / 评分镜像已就绪" or "2 项 digest 缺失" for detail/summary rows. */
export function imageDependencyLabel(template: EvalTemplate): string {
  const count = templatePreflightBlockers(template).length;
  return count ? `${count} 项 digest 缺失` : "执行 / 评分镜像已就绪";
}

export function imageDependencyDetail(template: EvalTemplate): string {
  if (templatePreflightBlockers(template).length) {
    return `数据集版本 ${template.dataset_version ?? "—"} 未登记，平台默认也未配置 · 启动会被阻断`;
  }
  return `数据集版本 ${template.dataset_version ?? "—"} 或平台默认已提供 digest`;
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

/** M10 (#410): the run row's source template line; custom runs render nothing. */
export function EvalTemplateOrigin({
  template,
  onOpen,
}: {
  template: EvaluationTemplateBrief | null | undefined;
  onOpen: (templateId: string) => void;
}) {
  if (!template) return null;
  return (
    <div className="eval-origin">
      <span className="eval-origin-badge">
        <Icon name="boxes" size={10} /> 来自模板
      </span>
      <button
        type="button"
        className="eval-origin-link"
        title={`查看模板：${template.name}`}
        onClick={() => onOpen(template.id)}
      >
        {template.name}
      </button>
    </div>
  );
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

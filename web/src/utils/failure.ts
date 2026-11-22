import type { EvalFailure } from "../api/evaluations";

/**
 * M8 (#375): one shared vocabulary for the console. The control plane stores
 * the runner's structured code; the UI must never fall back to showing raw
 * English node codes when a known category exists.
 */
export const FAILURE_LABELS: Record<string, string> = {
  model_rate_limited: "供应商限流",
  model_quota_exhausted: "配额耗尽",
  model_authentication_failed: "鉴权失败",
  model_unavailable: "模型不可用",
  model_overloaded: "供应商过载",
  agent_timeout: "Agent 超时",
  agent_crashed: "Agent 崩溃",
  agent_failed: "Agent 失败",
  asset_source_install_failed: "资产安装失败",
  asset_source_init_failed: "资产初始化失败",
  asset_source_inject_failed: "资产注入失败",
  asset_source_setup_failed: "资产构建失败",
  asset_source_setup_missing: "资产配置缺失",
  source_archive_invalid: "源码包无效",
  model_token_unavailable: "模型凭据不可用",
  scoring_failed: "评分失败",
  empty_patch: "未生成补丁",
  runner_failed: "执行失败",
  runner_timeout: "执行超时",
  runner_lost: "Runner 丢失",
  artifact_missing: "产物缺失",
  callback_retryable: "回调重试",
  callback_permanent: "回调失败",
  upload_failed: "上传失败",
  runtime_error: "运行时错误",
  execution_timeout: "执行超时",
};

const RETRYABLE_TONE = new Set(["model_rate_limited", "model_overloaded", "callback_retryable"]);

export interface FailureView {
  code: string;
  label: string;
  message: string | null;
  requestId: string | null;
  httpStatus: number | null;
  retryable: boolean | null;
  tone: "warning" | "error";
}

export function failureLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return FAILURE_LABELS[code] ?? code;
}

/**
 * Normalise a structured failure or a legacy ``error_code``/``error_summary``
 * pair into the same display shape. Old rows keep rendering without a
 * backfill; new rows gain the category plus upstream原文.
 */
export function failureView(
  failure: EvalFailure | null | undefined,
  errorCode: string | null | undefined,
  errorSummary: string | null | undefined,
): FailureView | null {
  if (failure) {
    const label = failureLabel(failure.code) ?? failure.code;
    return {
      code: failure.code,
      label,
      message: failure.message,
      requestId: failure.request_id,
      httpStatus: failure.http_status,
      retryable: failure.retryable,
      tone: RETRYABLE_TONE.has(failure.code) || failure.retryable === true ? "warning" : "error",
    };
  }
  if (!errorCode && !errorSummary) return null;
  const code = errorCode || "";
  const label = failureLabel(code) ?? errorSummary ?? "执行失败";
  const message =
    errorSummary && errorSummary !== code && errorSummary !== label ? errorSummary : null;
  return {
    code,
    label,
    message,
    requestId: null,
    httpStatus: null,
    retryable: null,
    tone: "error",
  };
}

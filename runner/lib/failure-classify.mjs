/**
 * Terminal failure classification for one real evaluation case (M8 #375).
 *
 * The runner is the single classification point: Claude Code only exposes an
 * exit code, and the platform must not guess from a node code. This module
 * reads the native stream/stderr tail and derives the structured fields the
 * completion callback and score report carry:
 *
 *   code       model_rate_limited / model_quota_exhausted /
 *              model_authentication_failed / agent_timeout / agent_crashed
 *   message    upstream原文 + HTTP status + request_id (bounded, redacted)
 *   phase      executing / assets / checkout / scoring
 *   retryable  provider rate limit only; quota/auth/crash stay permanent
 *
 * The classification is deliberately conservative: an unknown error becomes
 * ``agent_crashed`` (permanent) instead of silently retrying.
 */

const MAX_EVENT_TEXT = 600;
const MAX_MESSAGE = 1000;
const MAX_REQUEST_ID = 128;

const QUOTA_RE =
  /quota|insufficient[_\s-]?quota|token\s*plan|usage\s*limit|billing|额度|用量上限|配额|余额不足|套餐/i;
const RATE_RE =
  /rate[_\s-]?limit|rate\s+limited|too\s+many\s+requests|overloaded|限流|请求过多|稍后重试/i;
const AUTH_RE =
  /authentication|unauthori[sz]ed|invalid\s+(?:api\s*)?key|permission[_\s-]?denied|鉴权|认证失败|无权限/i;
const TIMEOUT_RE =
  /timed?\s*out|timeout|etimedout|operation\s+timed?\s*out|deadline\s+exceeded|超时/i;
const REQUEST_ID_RE =
  /(?:x-request-id|request[_-]?id)["']?\s*[:=]\s*["']?([A-Za-z0-9][A-Za-z0-9._:-]{2,})/i;
const HTTP_STATUS_RE = /(?:HTTP\s*|status\s*["']?\s*[:=]\s*["']?)([1-5][0-9]{2})\b/i;

function oneLine(value, limit = MAX_MESSAGE) {
  const text = String(value ?? "")
    .replace(/\s+/g, " ")
    .trim();
  if (text.length <= limit) return text;
  return text.slice(0, Math.max(0, limit - 1)) + "…";
}

function recordText(record) {
  if (typeof record === "string") return record;
  if (!record || typeof record !== "object") return "";
  const pieces = [];
  for (const key of ["error", "message", "content", "result", "detail", "text", "type", "code"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) pieces.push(value);
    else if (value && typeof value === "object") pieces.push(recordText(value));
  }
  return pieces.filter(Boolean).join(" ");
}

/**
 * Collect the last terminal error events from Claude Code's stream-json plus
 * the stderr tail. The newest event wins; older retry attempts stay out of the
 * message so a recovered 429 does not mask a later quota failure.
 */
export function terminalErrorText(traceText, stderr = "") {
  const candidates = [];
  const lines = String(traceText ?? "").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("{")) continue;
    let record;
    try {
      record = JSON.parse(trimmed);
    } catch {
      continue;
    }
    if (!record || typeof record !== "object") continue;
    const type = record.type;
    const subtype = record.subtype;
    const detail = recordText(record);
    const requestId = record.request_id ?? record.requestId;
    const status = record.status ?? record.http_status;
    const meta = [
      typeof requestId === "string" && requestId ? `request_id=${requestId}` : "",
      Number.isInteger(status) ? `status=${status}` : "",
    ]
      .filter(Boolean)
      .join(" ");
    const lineText = [detail, meta].filter(Boolean).join(" ");
    if (type === "system" && (subtype === "api_error" || subtype === "error")) {
      candidates.push(lineText);
    } else if (type === "result" && (record.is_error === true || record.subtype === "error")) {
      candidates.push(lineText);
    } else if (type === "error") {
      candidates.push(lineText);
    }
  }
  const stderrText = oneLine(stderr, 4000);
  // Only the newest terminal event is authoritative: an early 429 that Claude
  // Code retried successfully must not mask a later auth/exit failure. The
  // stderr tail is a fallback when the stream recorded no usable cause.
  let text = candidates.filter(Boolean).at(-1) || "";
  const hasSignal =
    HTTP_STATUS_RE.test(text) ||
    QUOTA_RE.test(text) ||
    RATE_RE.test(text) ||
    AUTH_RE.test(text) ||
    TIMEOUT_RE.test(text);
  if (!text || (!hasSignal && stderrText)) {
    text = [text, stderrText].filter(Boolean).join(" ");
  }
  return oneLine(text, 8000);
}

function requestIdFrom(text) {
  const match = REQUEST_ID_RE.exec(text || "");
  return match ? match[1].slice(0, MAX_REQUEST_ID) : null;
}

function httpStatusFrom(text) {
  const match = HTTP_STATUS_RE.exec(text || "");
  if (!match) return null;
  const value = Number(match[1]);
  return value >= 100 && value <= 599 ? value : null;
}

function describe(code, text, { exitStatus, httpStatus } = {}) {
  const status = httpStatus ? `HTTP ${httpStatus}` : "";
  switch (code) {
    case "model_quota_exhausted":
      return oneLine(`模型供应商 429 配额耗尽${status && status !== "HTTP 429" ? `（${status}）` : ""}：${text}`);
    case "model_rate_limited":
      return oneLine(`模型供应商 429 限流：${text}`);
    case "model_authentication_failed":
      return oneLine(`模型鉴权失败${status ? `（${status}）` : ""}：${text}`);
    case "model_unavailable":
      return oneLine(`模型供应商不可用${status ? `（${status}）` : ""}：${text}`);
    case "agent_timeout":
      return oneLine(`Agent 执行超时：${text || "未在预算内结束"}`);
    default:
      return oneLine(
        `Agent 异常退出${Number.isInteger(exitStatus) ? `（exit ${exitStatus}）` : ""}：${text || "无错误输出"}`,
      );
  }
}

/**
 * Classify a non-zero Claude Code exit.
 *
 * @param {object} input
 * @param {string} [input.traceText] raw claude-stream.jsonl
 * @param {string} [input.stderr] container stderr tail
 * @param {string} [input.stdout] container stdout tail
 * @param {number|null} [input.exitStatus]
 * @param {boolean} [input.timedOut]
 * @param {string} [input.phase="executing"]
 * @returns {{code:string,message:string,phase:string,request_id?:string,http_status?:number,retryable:boolean}}
 */
export function classifyAgentFailure({
  traceText = "",
  stderr = "",
  stdout = "",
  exitStatus = null,
  timedOut = false,
  phase = "executing",
} = {}) {
  const text = terminalErrorText(traceText, `${stderr}\n${stdout}`) || oneLine(stderr || stdout, 4000);
  const httpStatus = httpStatusFrom(text);
  let code = "agent_crashed";
  let retryable = false;

  if (timedOut) {
    code = "agent_timeout";
  } else if (httpStatus === 401 || httpStatus === 403 || AUTH_RE.test(text)) {
    code = "model_authentication_failed";
  } else if (typeof httpStatus === "number" && httpStatus >= 500) {
    code = "model_unavailable";
    retryable = true;
  } else if (httpStatus === 429 || RATE_RE.test(text) || QUOTA_RE.test(text)) {
    if (QUOTA_RE.test(text)) {
      code = "model_quota_exhausted";
    } else {
      code = "model_rate_limited";
      retryable = true;
    }
  } else if (TIMEOUT_RE.test(text)) {
    code = "agent_timeout";
  }

  const requestId = requestIdFrom(text);
  // Claude Code's api_error may omit the numeric status; the provider class
  // itself is authoritative (rate/quota are always an upstream 429).
  const effectiveStatus =
    httpStatus ??
    (code === "model_rate_limited" || code === "model_quota_exhausted"
      ? 429
      : code === "model_authentication_failed"
        ? 401
        : null);
  const message = describe(code, text || "", { exitStatus, httpStatus: effectiveStatus });
  const failure = {
    code,
    message,
    phase: phase || "executing",
    retryable,
  };
  if (requestId) failure.request_id = requestId;
  if (effectiveStatus) failure.http_status = effectiveStatus;
  return failure;
}

/** Build the structured detail for a setup/asset/checkout failure. */
export function buildFailureDetail({ code, message, phase = null, retryable = false, extra = {} } = {}) {
  const failure = {
    code: String(code || "runtime_error"),
    message: oneLine(message || code || "未知失败"),
    retryable: Boolean(retryable),
  };
  if (phase) failure.phase = phase;
  const requestId = requestIdFrom(failure.message);
  if (requestId) failure.request_id = requestId;
  return { ...failure, ...extra };
}

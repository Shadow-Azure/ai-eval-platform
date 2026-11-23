import type { EvaluationEvent } from "../../api/evaluations";

/**
 * Human-readable projection of evaluation events.
 *
 * The event API stores whatever the writer needed for diagnosis, including
 * object lists such as `trace.ingestion.updated`'s `files`. Rendering that
 * payload directly turns one event into a wall of JSON, so every surface
 * (overview timeline and full log) reads events through this model instead.
 */

export type EventTone = "default" | "success" | "danger";

export const TRACE_INGESTION_EVENT = "trace.ingestion.updated";

export const EVENT_TYPE_LABELS: Record<string, string> = {
  "eval.created": "评测已创建",
  "eval.dispatched": "分片已派发",
  "eval.dispatch_failed": "分片派发失败",
  "eval.cancelling": "取消处理中",
  "eval.cancel_requested": "已请求取消 GitHub 作业",
  "eval.cancel_failed": "取消 GitHub 作业失败",
  "eval.cancel_lookup_failed": "查找 GitHub 作业失败",
  "eval.cancelled": "评测已取消",
  "eval.budget_pausing": "预算用尽，暂停收口",
  "eval.budget_paused": "进入预算暂停",
  "eval.budget_resumed": "增加预算并恢复",
  "eval.retry_failed": "批量重试失败用例",
  "eval.spot_fallback": "切换到回退资源池",
  "case.started": "用例开始执行",
  "case.heartbeat": "用例心跳",
  "case.uploaded": "对象上传清单",
  "case.completed": "用例完成",
  "case.reconciled_completed": "用例补记完成",
  "case.failed": "用例失败",
  "case.timeout": "用例超时",
  "case.runner_lost": "Runner 失联",
  "case.spot_interrupted": "竞价实例中断",
  "case.retry_queued": "自动重试已排队",
  "case.retry_requested": "手动重试已排队",
  "case.artifact_missing": "结果对象缺失",
  "case.started_late": "迟到 started（保留审计）",
  "case.started_rejected": "启动被拒绝",
  "trace.ingestion.updated": "轨迹入库更新",
};

const TRACE_STATUS_TEXT: Record<string, string> = {
  pending: "待处理",
  ready: "可用",
  incomplete: "采集不完整",
  degraded: "已降级",
  failed: "失败",
  unlinked: "未关联",
};

/** Overview stays a key-event digest: heartbeats and audit rows belong to the log. */
const OVERVIEW_EVENT_TYPES = new Set<string>([
  "eval.created",
  "eval.dispatched",
  "eval.dispatch_failed",
  "eval.cancelling",
  "eval.cancelled",
  "eval.budget_pausing",
  "eval.budget_paused",
  "eval.budget_resumed",
  "eval.retry_failed",
  "eval.spot_fallback",
  "case.started",
  "case.completed",
  "case.reconciled_completed",
  "case.failed",
  "case.timeout",
  "case.runner_lost",
  "case.spot_interrupted",
  "case.retry_queued",
  "case.retry_requested",
  "case.artifact_missing",
  "case.started_rejected",
  TRACE_INGESTION_EVENT,
]);

/** At most this many ingestion snapshots when real lifecycle events exist. */
const INGESTION_OVERVIEW_SLOTS = 2;

export interface EventCopy {
  /** Case key, shard or run-level subject shown in bold in the timeline. */
  subject: string;
  action: string;
  context: string;
  tone: EventTone;
}

export interface EventTimelineItem extends EventCopy {
  id: number;
  eventType: string;
  typeLabel: string;
  clock: string;
  occurredAt: string | null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asText(value: unknown): string | null {
  if (typeof value === "string") return value.trim() ? value.trim() : null;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return String(value);
  return null;
}

function asCount(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.trunc(parsed) : null;
  }
  return null;
}

function truncate(value: string, limit = 80): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, limit - 1)}…` : normalized;
}

function caseSubject(
  event: EvaluationEvent,
  caseKeys: Map<string, string>,
): string {
  const payloadKey = asText(event.payload.case_key);
  if (payloadKey) return payloadKey;
  if (event.case_run_id) {
    return caseKeys.get(event.case_run_id) ?? event.case_run_id;
  }
  return "用例";
}

function ingestionCopy(
  event: EvaluationEvent,
  subject: string,
): EventCopy {
  const payload = event.payload;
  const counts = asRecord(payload.counts) ?? asRecord(payload.file_counts);
  const ready = asCount(counts?.ready) ?? 0;
  const pending = asCount(counts?.pending) ?? 0;
  const failed = asCount(counts?.failed) ?? 0;
  const degraded = asCount(counts?.degraded) ?? 0;
  const expected = asCount(counts?.expected) ?? asCount(counts?.total);
  const parts: string[] = [];
  if (ready) parts.push(`${ready} 个文件 ready`);
  if (degraded) parts.push(`${degraded} 个文件已降级`);
  if (pending) parts.push(`${pending} 个文件 pending`);
  if (failed) parts.push(`${failed} 个文件 failed`);
  const action = parts.length
    ? `轨迹入库：${parts.join(" · ")}`
    : `轨迹入库：${expected !== null ? `${expected} 个文件待处理` : "暂无文件状态"}`;
  const status = asText(payload.trace_status);
  const contextBits: string[] = [];
  if (status) contextBits.push(`轨迹${TRACE_STATUS_TEXT[status] ?? status}`);
  if (event.attempt_no !== null) contextBits.push(`attempt ${event.attempt_no}`);
  if (expected !== null && expected !== ready + pending + failed + degraded) {
    contextBits.push(`预期 ${expected} 个文件`);
  }
  const error = asText(payload.error_summary);
  if (error) contextBits.push(`错误：${truncate(error, 60)}`);
  return {
    subject,
    action,
    context: contextBits.join(" · "),
    tone: failed ? "danger" : status === "ready" ? "success" : "default",
  };
}

function runSubject(event: EvaluationEvent): string {
  const shard = asText(event.payload.shard_id);
  return shard ? `分片 ${shard}` : "调度器";
}

/** Project one event into the short, human-readable copy used by both surfaces. */
export function describeEvent(
  event: EvaluationEvent,
  caseKeys: Map<string, string>,
): EventCopy {
  const payload = event.payload;
  const caseKey = caseSubject(event, caseKeys);
  const attempt = event.attempt_no !== null ? `attempt ${event.attempt_no}` : "";
  const result = asText(payload.result_status);

  switch (event.event_type) {
    case TRACE_INGESTION_EVENT:
      return ingestionCopy(event, caseKey);
    case "eval.created":
      return {
        subject: "评测",
        action: `创建 ${asCount(payload.case_count) ?? "?"} 个用例，${asCount(payload.shard_count) ?? "?"} 个分片`,
        context: "",
        tone: "default",
      };
    case "eval.dispatched": {
      const status = asText(payload.github_status);
      return {
        subject: runSubject(event),
        action: "派发成功",
        context: [status ? `GitHub ${status}` : "", attempt].filter(Boolean).join(" · "),
        tone: "success",
      };
    }
    case "eval.dispatch_failed":
      return {
        subject: runSubject(event),
        action: "派发失败",
        context: truncate(asText(payload.last_error) ?? asText(payload.error) ?? "未知原因", 90),
        tone: "danger",
      };
    case "eval.cancelling":
      return {
        subject: "评测",
        action: "取消处理中",
        context: asText(payload.reason) ? `原因 ${truncate(asText(payload.reason) as string, 40)}` : "",
        tone: "default",
      };
    case "eval.cancel_requested":
      return { subject: "调度器", action: "已请求取消 GitHub 作业", context: "", tone: "default" };
    case "eval.cancel_failed":
    case "eval.cancel_lookup_failed":
      return {
        subject: "调度器",
        action: "取消 GitHub 作业失败",
        context: truncate(asText(payload.error) ?? "", 90),
        tone: "danger",
      };
    case "eval.cancelled":
      return {
        subject: "评测",
        action: "已取消",
        context: asText(payload.reason) ? `原因 ${truncate(asText(payload.reason) as string, 40)}` : "",
        tone: "default",
      };
    case "eval.budget_pausing":
      return {
        subject: "评测",
        action: "预算用尽，暂停收口",
        context: asText(payload.spent_cny) ? `已花费 ${asText(payload.spent_cny)} 元` : "",
        tone: "default",
      };
    case "eval.budget_paused":
      return {
        subject: "评测",
        action: "进入预算暂停",
        context: asText(payload.spent_cny) ? `已花费 ${asText(payload.spent_cny)} 元` : "",
        tone: "default",
      };
    case "eval.budget_resumed":
      return {
        subject: "评测",
        action: "增加预算并恢复",
        context: [
          asText(payload.additional_budget_cny) ? `增加 ${asText(payload.additional_budget_cny)} 元` : "",
          asCount(payload.attempts_created) ? `恢复 ${asCount(payload.attempts_created)} 个用例` : "",
        ].filter(Boolean).join(" · "),
        tone: "success",
      };
    case "eval.retry_failed":
      return {
        subject: "评测",
        action: "批量重试失败用例",
        context: `重试 ${asCount(payload.retried) ?? 0} 个 · 跳过 ${asCount(payload.skipped) ?? 0} 个`,
        tone: "default",
      };
    case "eval.spot_fallback":
      return {
        subject: "调度器",
        action: "切换到回退资源池",
        context: truncate(
          asText(payload.reason) ?? asText(payload.detail) ?? "",
          90,
        ),
        tone: "default",
      };
    case "case.started":
      return {
        subject: caseKey,
        action: "开始执行",
        context: attempt,
        tone: "default",
      };
    case "case.completed":
    case "case.reconciled_completed": {
      const action =
        result === "resolved"
          ? "评分完成，已解决"
          : result === "unresolved"
            ? "评分完成，未解决"
            : event.event_type === "case.reconciled_completed"
              ? "补记完成"
              : "执行完成";
      return {
        subject: caseKey,
        action,
        context: attempt,
        tone: result === "resolved" ? "success" : "default",
      };
    }
    case "case.failed": {
      const errorCode = asText(payload.error_code);
      const errorSummary = asText(payload.error_summary);
      return {
        subject: caseKey,
        action: errorCode ? `执行失败（${errorCode}）` : "执行失败",
        context: [attempt, errorSummary ? truncate(errorSummary, 70) : ""].filter(Boolean).join(" · "),
        tone: "danger",
      };
    }
    case "case.timeout":
      return {
        subject: caseKey,
        action: "执行超时",
        context: [attempt, asText(payload.error_summary) ? truncate(asText(payload.error_summary) as string, 70) : ""]
          .filter(Boolean)
          .join(" · "),
        tone: "danger",
      };
    case "case.runner_lost":
      return {
        subject: caseKey,
        action: "Runner 失联",
        context: [attempt, asText(payload.reason) ? truncate(asText(payload.reason) as string, 70) : ""]
          .filter(Boolean)
          .join(" · "),
        tone: "danger",
      };
    case "case.spot_interrupted":
      return {
        subject: caseKey,
        action: "竞价实例中断",
        context: [attempt, asText(payload.reason) ? truncate(asText(payload.reason) as string, 70) : ""]
          .filter(Boolean)
          .join(" · "),
        tone: "danger",
      };
    case "case.retry_queued":
      return {
        subject: caseKey,
        action: "自动重试已排队",
        context: attempt,
        tone: "default",
      };
    case "case.retry_requested":
      return {
        subject: caseKey,
        action: "手动重试已排队",
        context: [
          asText(payload.requested_by) ? `操作人 ${asText(payload.requested_by)}` : "",
          attempt,
        ].filter(Boolean).join(" · "),
        tone: "default",
      };
    case "case.artifact_missing": {
      const objects = Array.isArray(payload.objects) ? payload.objects.length : 0;
      return {
        subject: caseKey,
        action: "结果对象缺失",
        context: [objects ? `缺少 ${objects} 个对象` : "", attempt].filter(Boolean).join(" · "),
        tone: "danger",
      };
    }
    case "case.started_rejected":
      return {
        subject: caseKey,
        action: "启动被拒绝",
        context: [
          asText(payload.reason) ? `原因 ${truncate(asText(payload.reason) as string, 40)}` : "",
          attempt,
        ].filter(Boolean).join(" · "),
        tone: "danger",
      };
    case "case.heartbeat": {
      const phase = asText(payload.phase);
      return {
        subject: caseKey,
        action: "执行心跳",
        context: [phase ? `阶段 ${phase}` : "", attempt].filter(Boolean).join(" · "),
        tone: "default",
      };
    }
    default: {
      // Unknown event types still get one bounded, scalar-only line. Nested
      // payloads are deliberately ignored instead of stringified.
      const detailKeys = ["reason", "error", "status", "phase", "shard_id", "detail"];
      const details = detailKeys
        .map((key) => {
          const value = asText(payload[key]);
          return value ? `${key}=${truncate(value, 40)}` : "";
        })
        .filter(Boolean);
      return {
        subject: event.case_run_id ? caseKey : "评测",
        action: EVENT_TYPE_LABELS[event.event_type] ?? event.event_type,
        context: [attempt, details.join(" · ")].filter(Boolean).join(" · "),
        tone: event.event_type.includes("failed") || event.event_type.includes("timeout") ? "danger" : "default",
      };
    }
  }
}

/** Clock column for the overview timeline: `HH:MM`, or `—` when unparsable. */
export function clockLabel(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** One line for the full event log table. */
export function eventSummary(
  event: EvaluationEvent,
  caseKeys: Map<string, string>,
): string {
  const copy = describeEvent(event, caseKeys);
  // The log's "related object" column already shows the case key, so repeat it
  // only for run-level rows where that column says "评测".
  return [event.case_run_id ? "" : copy.subject, copy.action, copy.context]
    .filter(Boolean)
    .join(" · ");
}

/**
 * Pick the overview's recent timeline.
 *
 * `events` grow in ascending id order. The overview keeps key lifecycle rows,
 * folds repeated ingestion snapshots per attempt, and never lets ingestion
 * crowd out completions/failures/dispatches while still surfacing the newest
 * ingestion state when one exists.
 */
export function recentTimeline(
  events: EvaluationEvent[],
  caseKeys: Map<string, string>,
  limit = 6,
): EventTimelineItem[] {
  const ordered = [...events].sort((left, right) => right.id - left.id);
  const seenIngestion = new Set<string>();
  const ingestion: EvaluationEvent[] = [];
  const lifecycle: EvaluationEvent[] = [];

  for (const event of ordered) {
    if (!OVERVIEW_EVENT_TYPES.has(event.event_type)) continue;
    if (event.event_type === TRACE_INGESTION_EVENT) {
      const key = `${event.case_run_id ?? "-"}:${event.attempt_no ?? "-"}`;
      if (seenIngestion.has(key)) continue;
      seenIngestion.add(key);
      ingestion.push(event);
    } else {
      lifecycle.push(event);
    }
  }

  const ingestionSlots = ingestion.length
    ? Math.min(limit, lifecycle.length ? INGESTION_OVERVIEW_SLOTS : limit)
    : 0;
  const selected = [
    ...lifecycle.slice(0, limit - ingestionSlots),
    ...ingestion.slice(0, ingestionSlots),
  ];
  for (const event of lifecycle.slice(limit - ingestionSlots)) {
    if (selected.length >= limit) break;
    selected.push(event);
  }
  for (const event of ingestion.slice(ingestionSlots)) {
    if (selected.length >= limit) break;
    selected.push(event);
  }

  return selected
    .sort((left, right) => right.id - left.id)
    .map((event) => {
      const copy = describeEvent(event, caseKeys);
      return {
        ...copy,
        id: event.id,
        eventType: event.event_type,
        typeLabel: EVENT_TYPE_LABELS[event.event_type] ?? event.event_type,
        clock: clockLabel(event.occurred_at),
        occurredAt: event.occurred_at,
      };
    });
}

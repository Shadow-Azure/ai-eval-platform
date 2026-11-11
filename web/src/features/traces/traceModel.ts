export interface TraceSelection {
  attempt: number | null;
  session: string | null;
  span: string | null;
}

const SPAN_FILTERS = new Set([
  "all",
  "AGENT",
  "LLM",
  "TOOL",
  "MESSAGE",
  "THINKING",
  "CHAIN",
]);

export interface TraceSessionNode {
  id: string;
  parent_session_id: string | null;
  status: string;
  ordinal: number;
  agent_name?: string | null;
  children: TraceSessionNode[];
}

export interface FlatTraceSession<T extends TraceSessionNode = TraceSessionNode> {
  session: T;
  depth: number;
}

export interface FlatSessionTree<T extends TraceSessionNode = TraceSessionNode> {
  linked: FlatTraceSession<T>[];
  unlinked: FlatTraceSession<T>[];
  defaultSessionId: string | null;
}

export interface SpanLike {
  id: string;
  ordinal: number;
  kind?: string;
  parent_span_id?: string | null;
  parent_missing?: boolean;
  started_at?: string | null;
  ended_at?: string | null;
}

export interface SessionSpanLike extends SpanLike {
  session_id: string;
}

export interface SpanWindow<T extends SpanLike> {
  span: T;
  leftPercent: number;
  widthPercent: number;
  durationMs: number | null;
}

export interface SpanWindows<T extends SpanLike> {
  mode: "time" | "ordinal";
  rows: SpanWindow<T>[];
}

function trimmed(value: string | null | undefined): string | null {
  const result = value?.trim();
  return result ? result : null;
}

export function readTraceSelection(search: string): TraceSelection {
  const params = new URLSearchParams(search);
  const rawAttempt = Number(params.get("attempt"));
  return {
    attempt:
      Number.isInteger(rawAttempt) && rawAttempt > 0 ? rawAttempt : null,
    session: trimmed(params.get("session")),
    span: trimmed(params.get("span")),
  };
}

export function readSpanFilter(search: string): string {
  const value = new URLSearchParams(search).get("kind")?.trim().toUpperCase() ?? "all";
  return SPAN_FILTERS.has(value) ? value : "all";
}

export function flattenSessionTree<T extends TraceSessionNode>(
  sessions: T[],
): FlatSessionTree<T> {
  const known = new Set<string>();
  const collect = (nodes: TraceSessionNode[]) => {
    for (const node of nodes) {
      known.add(node.id);
      collect(node.children);
    }
  };
  collect(sessions);

  const linked: FlatTraceSession<T>[] = [];
  const unlinked: FlatTraceSession<T>[] = [];
  const seen = new Set<string>();
  const visit = (node: TraceSessionNode, depth: number) => {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    const parentMissing =
      node.parent_session_id !== null && !known.has(node.parent_session_id);
    if (node.status === "unlinked" || parentMissing) {
      unlinked.push({ session: node as T, depth: 0 });
    } else {
      linked.push({ session: node as T, depth });
    }
    for (const child of node.children) {
      visit(child, linked.some((item) => item.session.id === node.id) ? depth + 1 : 1);
    }
  };
  for (const root of sessions) visit(root, 0);
  return {
    linked,
    unlinked,
    defaultSessionId:
      linked.find((item) => item.depth === 0)?.session.id ??
      unlinked[0]?.session.id ??
      null,
  };
}

export function mergeSpanPages<T extends { id: string }>(
  current: T[],
  incoming: T[],
): T[] {
  const byId = new Map(current.map((item) => [item.id, item]));
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()];
}

export function spanBelongsToSession<T extends { session_id: string }>(
  span: T | null | undefined,
  sessionId: string | null,
): span is T {
  return Boolean(span && sessionId && span.session_id === sessionId);
}

export function mergeSelectedSpan<T extends SessionSpanLike>(
  spans: T[],
  selected: T | null | undefined,
  sessionId: string | null,
  filter = "all",
): T[] {
  if (
    !spanBelongsToSession(selected, sessionId) ||
    (filter !== "all" && selected.kind !== filter)
  ) {
    return spans;
  }
  const byId = new Map(spans.map((span) => [span.id, span]));
  byId.set(selected.id, selected);
  return [...byId.values()].sort((left, right) => {
    if (left.ordinal !== right.ordinal) return left.ordinal - right.ordinal;
    return left.id.localeCompare(right.id);
  });
}

export function markMissingParents<T extends SpanLike>(spans: T[]): T[] {
  const ids = new Set(spans.map((span) => span.id));
  return spans.map((span) => ({
    ...span,
    parent_missing:
      span.parent_missing === true ||
      (Boolean(span.parent_span_id) && !ids.has(span.parent_span_id as string)),
  }));
}

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function buildSpanWindows<T extends SpanLike>(
  spans: T[],
  clockComparable: boolean,
): SpanWindows<T> {
  const count = Math.max(1, spans.length);
  if (!clockComparable) {
    return {
      mode: "ordinal",
      rows: spans.map((span, index) => ({
        span,
        leftPercent: (index / count) * 100,
        widthPercent: Math.max(4, (0.82 / count) * 100),
        durationMs: null,
      })),
    };
  }

  const starts = spans
    .map((span) => timestamp(span.started_at))
    .filter((value): value is number => value !== null);
  const ends = spans
    .map((span) => timestamp(span.ended_at))
    .filter((value): value is number => value !== null);
  if (starts.length === 0 || ends.length === 0) {
    return buildSpanWindows(spans, false);
  }
  const base = Math.min(...starts);
  const finish = Math.max(...ends);
  const total = Math.max(1, finish - base);
  return {
    mode: "time",
    rows: spans.map((span) => {
      const started = timestamp(span.started_at);
      const ended = timestamp(span.ended_at);
      const valid = started !== null && ended !== null && ended >= started;
      const left = valid ? ((started - base) / total) * 100 : 0;
      const width = valid ? ((ended - started) / total) * 100 : 1.5;
      return {
        span,
        leftPercent: Math.max(0, Math.min(100, Number(left.toFixed(4)))),
        widthPercent: Math.max(0.8, Math.min(100, Number(width.toFixed(4)))),
        durationMs: valid ? ended - started : null,
      };
    }),
  };
}

export function requestIsCurrent(
  requestGeneration: number,
  activeGeneration: number,
): boolean {
  return requestGeneration === activeGeneration;
}

export function traceStatusForAttempt(options: {
  caseStatus: string;
  latestAttempt: boolean;
  fileStatuses: string[];
}): string {
  if (options.fileStatuses.includes("failed")) return "failed";
  if (options.fileStatuses.includes("degraded")) return "degraded";
  if (options.fileStatuses.length === 0) return "pending";
  if (options.latestAttempt && options.caseStatus) return options.caseStatus;
  return "ready";
}

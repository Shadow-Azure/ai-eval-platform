/**
 * Pure helpers behind the trace archive modal.
 *
 * No React or DOM imports: the page composes these values, `node:test` exercises
 * them directly. Session roles mirror the backend ZIP layout:
 * root-session files -> `main/`, child sessions -> `subagents/<agent>/`,
 * sessions without a resolvable parent -> `unlinked/`.
 */

export type TraceExportSessionRole = "main" | "subagent" | "unlinked";

export interface TraceExportSessionInput {
  id: string;
  trace_file_id?: string | null;
  native_session_id?: string | null;
  parent_session_id: string | null;
  status?: string | null;
  ordinal?: number | null;
  agent_name?: string | null;
  agent_id?: string | null;
  span_count?: number | null;
  children: TraceExportSessionInput[];
}

export interface TraceExportFileInput {
  id?: string;
  size: number;
  logical_path?: string | null;
}

export interface TraceExportSessionRow {
  id: string;
  nativeSessionId: string;
  name: string;
  role: TraceExportSessionRole;
  roleLabel: string;
  agentId: string | null;
  spanCount: number;
  depth: number;
  parentId: string | null;
  /** Native file linked to the session, when the API exposes `trace_file_id`. */
  fileId: string | null;
  fileName: string | null;
  fileSize: number;
  fileCount: number;
}

export interface TraceExportOrphanFile {
  id: string | null;
  name: string;
  size: number;
}

export interface TraceExportSummary {
  /** Every session once, in deterministic pre-order. */
  sessions: TraceExportSessionRow[];
  main: TraceExportSessionRow[];
  subagents: TraceExportSessionRow[];
  unlinked: TraceExportSessionRow[];
  /** Native files with no linked session; the backend archives them under unlinked/. */
  orphanFiles: TraceExportOrphanFile[];
  mainAgentCount: number;
  subagentCount: number;
  unlinkedCount: number;
  sessionCount: number;
  rawFileCount: number;
  totalSize: number;
  /** ZIP stores native files without re-serialization; payload + manifest. */
  estimatedArchiveSize: number;
}

const ROLE_LABELS: Record<TraceExportSessionRole, string> = {
  main: "主 Agent",
  subagent: "子 Agent",
  unlinked: "未关联",
};

/** The manifest itself is small but grows with the file/session lists. */
const MANIFEST_RESERVE_BYTES = 8192;

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

function finiteSize(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}

function trimmed(value: string | null | undefined): string | null {
  const result = value?.trim();
  return result ? result : null;
}

function byOrdinalThenId(
  left: TraceExportSessionInput,
  right: TraceExportSessionInput,
): number {
  const leftOrdinal = typeof left.ordinal === "number" ? left.ordinal : null;
  const rightOrdinal = typeof right.ordinal === "number" ? right.ordinal : null;
  if (leftOrdinal !== null && rightOrdinal !== null && leftOrdinal !== rightOrdinal) {
    return leftOrdinal - rightOrdinal;
  }
  return left.id.localeCompare(right.id);
}

function sessionName(
  session: TraceExportSessionInput,
  role: TraceExportSessionRole,
): string {
  const named = trimmed(session.agent_name) ?? trimmed(session.agent_id);
  if (named) return named;
  if (role === "main") return "Main agent";
  return session.id;
}

function basename(logicalPath: string | null | undefined): string | null {
  const raw = trimmed(logicalPath);
  if (!raw) return null;
  const name = raw.replace(/\\/g, "/").split("/").pop()?.trim();
  return name || null;
}

function collectKnownIds(sessions: TraceExportSessionInput[]): Set<string> {
  const known = new Set<string>();
  const visit = (node: TraceExportSessionInput) => {
    known.add(node.id);
    node.children.forEach(visit);
  };
  sessions.forEach(visit);
  return known;
}

/**
 * Flatten the normalized session tree into display rows.
 *
 * A session is unlinked when the API marks it `unlinked` or when its parent is
 * missing from the tree; everything else under a root is a subagent. Duplicate
 * ids are rendered once so a malformed payload cannot repeat rows.
 */
export function summarizeTraceExport(
  sessions: TraceExportSessionInput[],
  files: TraceExportFileInput[] = [],
): TraceExportSummary {
  const known = collectKnownIds(sessions);
  const filesById = new Map(
    files
      .filter((file): file is TraceExportFileInput & { id: string } => Boolean(file.id))
      .map((file) => [file.id, file]),
  );
  const rows: TraceExportSessionRow[] = [];
  const seen = new Set<string>();

  const visit = (session: TraceExportSessionInput, depth: number) => {
    if (seen.has(session.id)) return;
    seen.add(session.id);
    const parentId = trimmed(session.parent_session_id);
    // Only a session without a parent is a main/root session; a child nested in
    // the tree that lost its parent is unlinked, never promoted to main.
    const root = parentId === null;
    const parentMissing = parentId !== null && !known.has(parentId);
    const role: TraceExportSessionRole =
      session.status === "unlinked" || parentMissing
        ? "unlinked"
        : root
          ? "main"
          : "subagent";
    const file = session.trace_file_id
      ? filesById.get(session.trace_file_id)
      : undefined;
    rows.push({
      id: session.id,
      nativeSessionId: trimmed(session.native_session_id) ?? session.id,
      name: sessionName(session, role),
      role,
      roleLabel: ROLE_LABELS[role],
      agentId: trimmed(session.agent_id),
      spanCount: finiteSize(session.span_count),
      depth: role === "unlinked" ? 0 : depth,
      parentId,
      fileId: file?.id ?? null,
      fileName: basename(file?.logical_path),
      fileSize: finiteSize(file?.size),
      fileCount: file ? 1 : 0,
    });
    const children = [...session.children].sort(byOrdinalThenId);
    for (const child of children) visit(child, depth + 1);
  };

  const roots = [...sessions].sort(byOrdinalThenId);
  for (const root of roots) visit(root, 0);

  const main = rows.filter((row) => row.role === "main");
  const subagents = rows.filter((row) => row.role === "subagent");
  const unlinked = rows.filter((row) => row.role === "unlinked");
  const totalSize = files.reduce((sum, file) => sum + finiteSize(file.size), 0);
  const rawFileCount = files.length;
  const referenced = new Set(
    rows.map((row) => row.fileId).filter((id): id is string => Boolean(id)),
  );
  const orphanFiles: TraceExportOrphanFile[] = files
    .filter((file) => !file.id || !referenced.has(file.id))
    .map((file) => ({
      id: file.id ?? null,
      name: basename(file.logical_path) ?? file.id ?? "trace-file",
      size: finiteSize(file.size),
    }));

  return {
    sessions: [...main, ...subagents, ...unlinked],
    main,
    subagents,
    unlinked,
    orphanFiles,
    mainAgentCount: main.length,
    subagentCount: subagents.length,
    unlinkedCount: unlinked.length,
    sessionCount: rows.length,
    rawFileCount,
    totalSize,
    estimatedArchiveSize:
      rawFileCount === 0 ? 0 : totalSize + MANIFEST_RESERVE_BYTES,
  };
}

/** Human byte labels for the stats grid and the confirm button. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  if (unit === 0) return `${Math.round(value)} B`;
  const rounded = value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${BYTE_UNITS[unit]}`;
}

/**
 * Mirrors `trace_export.safe_segment`: basename only, strip every character
 * outside `A-Za-z0-9._-`, collapse `..` runs, drop leading dots, cap 160.
 * Returns null so the caller can pick a stable fallback.
 */
function safeSegment(value: string | null | undefined): string | null {
  const raw = trimmed(value);
  if (!raw) return null;
  const basename = raw.replace(/\\/g, "/").split("/").pop() ?? raw;
  let cleaned = basename.replace(/[^A-Za-z0-9._-]/g, "");
  while (cleaned.includes("..")) cleaned = cleaned.replace(/\.\./g, ".");
  cleaned = cleaned.replace(/^\.+/, "").trim();
  return cleaned ? cleaned.slice(0, 160) : null;
}

const CONTROL_CHARS = /[\u0000-\u001f\u007f]/g;

function safeArchiveFilename(raw: string | null): string | null {
  if (!raw) return null;
  const segment = raw
    .replace(CONTROL_CHARS, "")
    .split(/[\\/]/)
    .pop()
    ?.trim()
    .replace(/^"|"$/g, "")
    .replace(/^\.+/, "")
    .slice(0, 180);
  return segment ? segment : null;
}

function decodeExtendedFilename(value: string): string | null {
  // RFC 5987: charset'language'percent-encoded-value
  const parts = /^([^']*)'([^']*)'(.*)$/.exec(value);
  const encoded = parts ? parts[3] : value;
  try {
    return safeArchiveFilename(decodeURIComponent(encoded));
  } catch {
    return null;
  }
}

/**
 * Read the archive name from `Content-Disposition`.
 * `filename*` (RFC 5987, UTF-8) wins over the ASCII `filename` fallback.
 */
export function parseArchiveFilename(header: string | null): string | null {
  if (!header) return null;
  const extended = /filename\*\s*=\s*("([^"]*)"|[^;]+)/i.exec(header);
  if (extended) {
    const raw = (extended[2] ?? extended[1]).trim().replace(/^"|"$/g, "");
    const decoded = decodeExtendedFilename(raw);
    if (decoded) return decoded;
  }
  const plain = /filename\s*=\s*("([^"]*)"|[^;]+)/i.exec(header);
  if (plain) {
    return safeArchiveFilename((plain[2] ?? plain[1]).trim());
  }
  return null;
}


/** Mirrors the backend's `trace-<safe-case-key>-attempt-<n>` root folder. */
export function archiveRootName(
  caseKey: string | null | undefined,
  attemptNo: number | null | undefined,
  caseRunId?: string | null,
): string {
  const attempt =
    typeof attemptNo === "number" && Number.isInteger(attemptNo) && attemptNo > 0
      ? attemptNo
      : 1;
  const fallback =
    safeSegment(caseRunId ? `case-${caseRunId}` : null) ?? "case";
  return `trace-${safeSegment(caseKey) ?? fallback}-attempt-${attempt}`;
}

export interface TraceArchiveTreeOptions {
  caseKey: string | null | undefined;
  caseRunId?: string | null;
  attemptNo: number | null | undefined;
  main?: TraceExportSessionRow[];
  subagents: TraceExportSessionRow[];
  unlinked?: TraceExportSessionRow[];
  orphanFiles?: TraceExportOrphanFile[];
  unlinkedCount: number;
}

interface TreeEntry {
  label: string;
  children: TreeEntry[];
}

function fileEntries(
  rows: TraceExportSessionRow[] | undefined,
  claimed: Set<string>,
): TreeEntry[] {
  const names: string[] = [];
  for (const row of rows ?? []) {
    if (row.fileId && claimed.has(row.fileId)) continue;
    const name = safeSegment(row.fileName);
    if (!name || names.includes(name)) continue;
    names.push(name);
    if (row.fileId) claimed.add(row.fileId);
  }
  return names.map((name) => ({ label: name, children: [] }));
}

/**
 * Deterministic text preview of the archive layout.
 *
 * The per-file `main/` vs `subagents/<agent>/` assignment is owned by the
 * backend manifest (`files[].archive_path`), so folder and file names mirror
 * the same sanitization rules while the manifest stays authoritative.
 */
export function buildArchiveTree(options: TraceArchiveTreeOptions): string[] {
  const claimed = new Set<string>();
  const mainEntries = fileEntries(options.main, claimed);
  const folders = new Map<string, TreeEntry[]>();
  for (const row of options.subagents) {
    if (row.fileId && claimed.has(row.fileId)) continue;
    // `_session_directory` uses the agent name/id, or `session-<id>` when the
    // child session has neither.
    const unnamed = row.agentId === null && row.name === row.id;
    const folder = unnamed
      ? safeSegment(`session-${row.id}`)
      : (safeSegment(row.name) ?? safeSegment(`session-${row.id}`));
    if (!folder) continue;
    const entries = folders.get(folder) ?? [];
    const name = safeSegment(row.fileName);
    if (name && !entries.some((entry) => entry.label === name)) {
      entries.push({ label: name, children: [] });
      if (row.fileId) claimed.add(row.fileId);
    }
    folders.set(folder, entries);
  }

  const top: TreeEntry[] = [
    { label: "manifest.json", children: [] },
    { label: "main/", children: mainEntries },
  ];
  if (folders.size) {
    top.push({
      label: "subagents/",
      children: [...folders].map(([folder, children]) => ({
        label: `${folder}/`,
        children,
      })),
    });
  }
  const unlinkedEntries = fileEntries(options.unlinked, claimed);
  for (const file of options.orphanFiles ?? []) {
    if (file.id && claimed.has(file.id)) continue;
    const name = safeSegment(file.name);
    if (!name || unlinkedEntries.some((entry) => entry.label === name)) continue;
    unlinkedEntries.push({ label: name, children: [] });
    if (file.id) claimed.add(file.id);
  }
  if (options.unlinkedCount > 0 || unlinkedEntries.length > 0) {
    top.push({ label: "unlinked/", children: unlinkedEntries });
  }

  const lines = [
    `${archiveRootName(options.caseKey, options.attemptNo, options.caseRunId)}/`,
  ];
  const render = (entries: TreeEntry[], prefix: string) => {
    entries.forEach((entry, index) => {
      const last = index === entries.length - 1;
      lines.push(`${prefix}${last ? "└──" : "├──"} ${entry.label}`);
      render(entry.children, `${prefix}${last ? "    " : "│   "}`);
    });
  };
  render(top, "");
  return lines;
}

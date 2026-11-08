// 开发流程体系的纯逻辑层：```yaml flow 围栏解析、流程树装载、离线校验、
// glob/提交引用工具。规则 home：.agents/flow/README.md（封闭集合与本文件
// 互为镜像，改一处必改另一处）。本文件不直接读 git——仓库身份、文件存在性、
// diff 都由调用方注入，保持可测试。

import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

export const FLOW_ROOT = ".agents/flow";
// 与 .agents/flow/README.md「封闭集合」节互为镜像（spec 互检）。
export const ISSUE_STATUSES = ["backlog", "ready", "in-progress", "done"];
export const PRIORITIES = ["P0", "P1", "P2"];
export const MILESTONE_STATUSES = ["planned", "active", "done"];

const FENCE_OPEN = /^```yaml flow$/;
const FENCE_CLOSE = /^```$/;

// ── yaml 子集解析 ────────────────────────────────────────────────────────────
// 只支持本库 flow 围栏用到的形状：顶层 `key: scalar` / `key:` + 同列或缩进
// 的 `- item` 列表 / 缩进两格的一层嵌套 map；行内列表 `[a, b]`；标量只认
// null / true / false / 非负整数 / 裸字符串或引号字符串。超出即抛错。

function parseScalar(raw, file, lineNo) {
  const v = raw.trim();
  if (v === "null" || v === "~") return null;
  if (v === "true") return true;
  if (v === "false") return false;
  if (/^\d+$/.test(v)) return Number.parseInt(v, 10);
  const quoted = /^"(.*)"$/.exec(v) ?? /^'(.*)'$/.exec(v);
  if (quoted) return quoted[1];
  if (/[\r\t]/.test(v) || v !== v.trim())
    throw new Error(`${file}:${lineNo}: 无法解析的标量 ${JSON.stringify(v)}（yaml flow 子集见 .agents/flow/README.md）`);
  return v;
}

/** 解析 yaml flow 围栏体（行数组）为对象。 */
export function parseFlowYaml(lines, file) {
  const out = {};
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const lineNo = i + 1;
    if (!line.trim() || line.trim().startsWith("#")) {
      i++;
      continue;
    }
    const m = /^([a-z][a-z0-9-]*):(?:\s+(.*))?$/.exec(line);
    if (!m) throw new Error(`${file}:${lineNo}: 无法解析的行 ${JSON.stringify(line)}（期望 key: value）`);
    const [, key, rest] = m;
    if (rest !== undefined && rest.trim() !== "") {
      const v = rest.trim();
      if (v === "[]") out[key] = [];
      else if (v.startsWith("[") && v.endsWith("]"))
        out[key] = v.slice(1, -1).split(",").map((s) => parseScalar(s, file, lineNo));
      else out[key] = parseScalar(v, file, lineNo);
      i++;
      continue;
    }
    // 子行：列表项（同列或缩进的 "- item"）或缩进两格的一层嵌套 map。
    const list = [];
    const map = {};
    let saw = null;
    i++;
    while (i < lines.length) {
      const child = lines[i];
      if (!child.trim()) {
        i++;
        continue;
      }
      const listItem = /^\s*- (.*)$/.exec(child);
      const mapItem = /^ {2}([a-z][a-z0-9-]*):(?:\s+(.*))?$/.exec(child);
      if (listItem && saw !== "map") {
        saw = "list";
        list.push(parseScalar(listItem[1], file, i + 1));
        i++;
        continue;
      }
      if (!listItem && mapItem && saw !== "list") {
        saw = "map";
        if (mapItem[2] === undefined || mapItem[2].trim() === "")
          throw new Error(`${file}:${i + 1}: 嵌套 map 只支持一层（${mapItem[1]}: 不能为空）`);
        map[mapItem[1]] = parseScalar(mapItem[2], file, i + 1);
        i++;
        continue;
      }
      break;
    }
    out[key] = saw === "map" ? map : list;
  }
  return out;
}

/**
 * 解析一个 flow 工作文件：必须恰好一个 ```yaml flow 围栏。
 * @returns {{kind: string, data: object}}
 */
export function parseFlowDocument(text, file) {
  const lines = text.split("\n");
  const blocks = [];
  let open = null;
  lines.forEach((line, idx) => {
    if (open) {
      if (FENCE_CLOSE.test(line)) {
        blocks.push(open.lines);
        open = null;
      } else open.lines.push(line);
      return;
    }
    if (FENCE_OPEN.test(line)) open = { lines: [], start: idx + 1 };
  });
  if (open) throw new Error(`${file}: \`\`\`yaml flow 围栏未闭合`);
  if (blocks.length !== 1)
    throw new Error(`${file}: 须恰好一个 yaml flow 围栏（实有 ${blocks.length} 个）`);
  const data = parseFlowYaml(blocks[0], file);
  if (!["roadmap", "milestone", "issue"].includes(data.kind))
    throw new Error(`${file}: kind 必须是 roadmap | milestone | issue（实有 ${JSON.stringify(data.kind)}）`);
  return { kind: data.kind, data };
}

// ── 树装载 ───────────────────────────────────────────────────────────────────

async function collectDir(dir, suffix = ".md") {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.isFile() && entry.name.endsWith(suffix) && !entry.name.endsWith(".en.md"))
      out.push(path.join(dir, entry.name));
  }
  return out.sort();
}

/**
 * 装载 .agents/flow/ 树。解析错误与结构错误收进 errors（门禁要一次报全），
 * 不抛异常。返回 { tree, errors }。
 */
export async function loadFlowTree(root = FLOW_ROOT) {
  const errors = [];
  const tree = { roadmap: null, milestones: [], issues: [] };
  const roadmapFile = path.join(root, "roadmap.md");
  if (!existsSync(roadmapFile)) {
    errors.push(`${roadmapFile}: 不存在（流程树的唯一入口，见 .agents/flow/README.md）`);
    return { tree, errors };
  }
  const parse = async (file) => {
    try {
      return parseFlowDocument(await readFile(file, "utf8"), file);
    } catch (error) {
      errors.push(error.message);
      return null;
    }
  };
  const roadmap = await parse(roadmapFile);
  if (roadmap) {
    if (roadmap.kind !== "roadmap") errors.push(`${roadmapFile}: kind 必须是 roadmap`);
    tree.roadmap = { file: roadmapFile, data: roadmap.data };
  }
  for (const file of await collectDir(path.join(root, "milestones"))) {
    const parsed = await parse(file);
    if (!parsed) continue;
    if (parsed.kind !== "milestone") {
      errors.push(`${file}: kind 必须是 milestone`);
      continue;
    }
    tree.milestones.push({ file, name: path.basename(file, ".md"), data: parsed.data });
  }
  for (const file of await collectDir(path.join(root, "issues"))) {
    const parsed = await parse(file);
    if (!parsed) continue;
    if (parsed.kind !== "issue") {
      errors.push(`${file}: kind 必须是 issue`);
      continue;
    }
    tree.issues.push({ file, name: path.basename(file, ".md"), data: parsed.data });
  }
  return { tree, errors };
}

// ── 离线校验 ─────────────────────────────────────────────────────────────────

function checkGithub(file, data, repo, kindLabel, errors) {
  const gh = data.github;
  if (gh === null || typeof gh !== "object" || Array.isArray(gh)) {
    errors.push(`${file}: 缺 github 段（{number, url}，未同步时均 null）`);
    return;
  }
  const { number, url } = gh;
  if (number !== null && (!Number.isInteger(number) || number <= 0))
    errors.push(`${file}: github.number 必须是正整数或 null（实有 ${JSON.stringify(number)}）`);
  if (url !== null && typeof url !== "string")
    errors.push(`${file}: github.url 必须是字符串或 null`);
  if (url !== null && number === null)
    errors.push(`${file}: github.url 非空但 number 为 null`);
  if (typeof url === "string" && number !== null) {
    const suffix = kindLabel === "milestone" ? `milestone/${number}` : `issues/${number}`;
    if (!url.endsWith(`/${suffix}`))
      errors.push(`${file}: github.url 与 number 不一致（期望以 /${suffix} 结尾）`);
    if (repo && url !== `https://github.com/${repo}/${suffix}`)
      errors.push(`${file}: github.url 不属于本仓库 ${repo}（实有 ${url}）`);
  }
}

/**
 * 校验流程树（离线规则全集）。
 * @param {object} tree loadFlowTree 的 tree。
 * @param {{repo?: string, exists?: (absPath: string) => boolean}} opts
 *   repo 形如 "owner/name"（git remote 推导，不可得时跳过 URL 宿主校验）；
 *   exists 用于 adr 链接存在性（默认读真实文件系统）。
 * @returns {string[]} 错误列表（空 = 绿）。
 */
export function validateTree(tree, opts = {}) {
  const errors = [];
  const exists = opts.exists ?? ((p) => existsSync(p));
  const repo = opts.repo;
  if (!tree.roadmap) return ["roadmap 缺失或解析失败"];
  const order = tree.roadmap.data.milestones;
  if (!Array.isArray(order) || order.length === 0 || order.some((m) => typeof m !== "string"))
    errors.push(`${tree.roadmap.file}: milestones 必须是非空字符串列表`);

  // milestone：归属序列、文件名前缀、封闭集合、github 形状。
  const byId = new Map();
  for (const m of tree.milestones) {
    const { file, name, data } = m;
    if (typeof data.id !== "string" || !data.id) {
      errors.push(`${file}: 缺 id`);
      continue;
    }
    if (byId.has(data.id)) errors.push(`${file}: id ${data.id} 与 ${byId.get(data.id)} 重复`);
    byId.set(data.id, file);
    if (Array.isArray(order) && !order.includes(data.id))
      errors.push(`${file}: id ${data.id} 未登记进 roadmap 序列`);
    if (!name.startsWith(`${data.id}-`))
      errors.push(`${file}: 文件名必须以 id 开头（${data.id}-*）`);
    if (typeof data.title !== "string" || !data.title)
      errors.push(`${file}: 缺 title（GitHub milestone 同步用）`);
    if (!MILESTONE_STATUSES.includes(data.status))
      errors.push(`${file}: status 必须是 ${MILESTONE_STATUSES.join(" | ")}（实有 ${JSON.stringify(data.status)}）`);
    checkGithub(file, data, repo, "milestone", errors);
  }
  if (Array.isArray(order))
    for (const id of order)
      if (!byId.has(id)) errors.push(`${tree.roadmap.file}: 序列里的 ${id} 没有对应 milestone 文件`);
  if (Array.isArray(order) && new Set(order).size !== order.length)
    errors.push(`${tree.roadmap.file}: milestones 序列有重复项`);

  // issue：字段、github、draft/bootstrap、adr 链接。
  const issueNumbers = new Map();
  const milestoneNumbers = new Map();
  for (const m of tree.milestones) {
    const n = m.data.github?.number;
    if (Number.isInteger(n)) {
      if (milestoneNumbers.has(n)) errors.push(`${m.file}: github.number ${n} 与 ${milestoneNumbers.get(n)} 重复`);
      milestoneNumbers.set(n, m.file);
    }
  }
  const bootstraps = [];
  for (const issue of tree.issues) {
    const { file, name, data } = issue;
    if (typeof data.milestone !== "string" || !byId.has(data.milestone)) {
      errors.push(`${file}: milestone 必须引用 roadmap 序列内的 id（实有 ${JSON.stringify(data.milestone)}）`);
      continue;
    }
    if (!name.startsWith(`${data.milestone}-`))
      errors.push(`${file}: 文件名必须以所属 milestone id 开头（${data.milestone}-*）`);
    if (!PRIORITIES.includes(data.priority))
      errors.push(`${file}: priority 必须是 ${PRIORITIES.join(" | ")}（实有 ${JSON.stringify(data.priority)}）`);
    if (!ISSUE_STATUSES.includes(data.status))
      errors.push(`${file}: status 必须是 ${ISSUE_STATUSES.join(" | ")}（实有 ${JSON.stringify(data.status)}）`);
    if (!Array.isArray(data.scope) || data.scope.length === 0 || data.scope.some((s) => typeof s !== "string" || !s))
      errors.push(`${file}: scope 必须是非空 glob 列表（本 issue 允许触碰的文件面）`);
    if (data.bootstrap !== undefined && data.bootstrap !== true && data.bootstrap !== false)
      errors.push(`${file}: bootstrap 只能是 true/false`);
    if (data.bootstrap === true) bootstraps.push(issue);
    checkGithub(file, data, repo, "issue", errors);
    const n = data.github?.number;
    if (Number.isInteger(n)) {
      if (issueNumbers.has(n)) errors.push(`${file}: github.number ${n} 与 ${issueNumbers.get(n)} 重复`);
      issueNumbers.set(n, file);
    }
    if (n === null && data.bootstrap !== true && data.status !== "backlog")
      errors.push(`${file}: github.number 为 null 的 draft 只能处于 backlog（先 pnpm flow:sync 回填编号）`);
    if (!Array.isArray(data.adr)) errors.push(`${file}: adr 必须是列表（可空）`);
    else
      for (const ref of data.adr) {
        if (typeof ref !== "string" || !ref) {
          errors.push(`${file}: adr 项必须是非空相对路径`);
          continue;
        }
        const resolved = path.resolve(path.dirname(file), ref);
        if (!resolved.startsWith(path.resolve(".agents/notes") + path.sep))
          errors.push(`${file}: adr ${ref} 必须指向 .agents/notes/ 内（ADR 层复用 Agent Notes）`);
        else if (!exists(resolved)) errors.push(`${file}: adr 链接不存在 → ${ref}`);
      }
  }
  if (bootstraps.length > 1)
    errors.push(`bootstrap: true 全库至多一个（自举豁免），实有 ${bootstraps.map((b) => b.file).join("、")}`);
  if (bootstraps.length === 1 && Array.isArray(order) && bootstraps[0].data.milestone !== order[0])
    errors.push(`${bootstraps[0].file}: bootstrap issue 必须属于 roadmap 序列的第一个 milestone（${order[0]}）`);

  // milestone done ⟺ 其 issue 全 done。
  for (const m of tree.milestones) {
    const owned = tree.issues.filter((i) => i.data.milestone === m.data.id);
    const allDone = owned.length > 0 && owned.every((i) => i.data.status === "done");
    if (m.data.status === "done" && !allDone)
      errors.push(`${m.file}: status done 但尚有未 done 的 issue`);
    if (allDone && m.data.status !== "done")
      errors.push(`${m.file}: 全部 issue 已 done，milestone status 应为 done`);
  }

  // 优先级推进资格：约束 in-progress / done 的 issue。
  if (Array.isArray(order)) errors.push(...eligibilityErrors(tree.issues, order));
  return errors;
}

/**
 * 优先级推进规则（.agents/flow/README.md「优先级推进」的机械执行）：
 * 跨 milestone 同档串行；档内并行不限；最高未清档仅剩 1 个未 done 时下一档解锁。
 * 只约束 in-progress / done 的 issue。
 */
export function eligibilityErrors(issues, order) {
  const errors = [];
  const active = issues.filter((i) => i.data.status === "in-progress" || i.data.status === "done");
  for (const issue of active) {
    const { milestone, priority, status } = issue.data;
    const idx = order.indexOf(milestone);
    if (idx < 0) continue; // 归属错误由 validateTree 报
    // 跨 milestone 同档串行：之前 milestone 的同档 issue 须全部 done。
    for (const before of order.slice(0, idx)) {
      const blockers = issues.filter(
        (i) => i.data.milestone === before && i.data.priority === priority && i.data.status !== "done",
      );
      if (blockers.length > 0)
        errors.push(
          `${issue.file}: ${priority} 档跨 milestone 串行——${before} 尚有 ${blockers.length} 个同档 issue 未 done（${blockers.map((b) => b.name).join("、")}）`,
        );
    }
    // 档内退档：本 milestone 最高未清档 H；只有 H 档，或 H 仅剩 1 个未 done 时的下一档可动。
    const siblings = issues.filter((i) => i.data.milestone === milestone);
    const openTiers = PRIORITIES.filter((p) =>
      siblings.some((s) => s.data.priority === p && s.data.status !== "done"),
    );
    if (openTiers.length === 0) continue;
    const top = openTiers[0];
    const topRemaining = siblings.filter(
      (s) => s.data.priority === top && s.data.status !== "done",
    ).length;
    // 已清更高档的历史 active issue 不重新评估：否则某档全部 done、里程碑进入
    // 低档后，先前合法关闭的 P0 会被“当前最高档是 P1”反向判死。
    if (PRIORITIES.indexOf(priority) < PRIORITIES.indexOf(top)) continue;
    if (priority === top) continue;
    const next = PRIORITIES[PRIORITIES.indexOf(top) + 1];
    if (next && priority === next && topRemaining === 1) continue;
    errors.push(
      `${issue.file}: ${milestone} 当前最高未清档是 ${top}（剩 ${topRemaining} 个），${priority} 档未解锁（status: ${status}）`,
    );
  }
  return errors;
}

// ── diff 模式工具（CI on PR）─────────────────────────────────────────────────

/** 极简 glob 转 RegExp：双星跨路径段、单星段内、问号段内单字符。 */
export function globToRegExp(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*") {
      if (glob[i + 1] === "*") {
        if (glob[i + 2] === "/") {
          re += "(?:[^/]+/)*";
          i += 2;
        } else {
          re += ".*";
          i += 1;
        }
      } else re += "[^/]*";
    } else if (c === "?") re += "[^/]";
    else re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${re}$`);
}

/** 提交/PR 标题里的 issue 引用：(#12) 或 [#12]。 */
export function collectRefs(subject) {
  return [...subject.matchAll(/[([]#(\d+)[)\]]/g)].map((m) => Number.parseInt(m[1], 10));
}

/** 不在任一 scope glob 覆盖下的文件列表。 */
export function uncoveredFiles(files, scopes) {
  const regexes = scopes.map(globToRegExp);
  return files.filter((f) => !regexes.some((re) => re.test(f)));
}

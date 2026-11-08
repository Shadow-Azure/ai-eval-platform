// 双语配对的纯逻辑层：三件套路径推导、blob hash、结构签名、生成区切分、
// 链接目标解析与锚点推导、配对记录渲染/解析、豁免 manifest、CLI 解析。与 CLI
// （verify-translation-pairing.mjs）分离：本文件不读不写仓库树，只做可复用的推导。
// 契约 home：docs/i18n/README.md。
// 结构签名用自带的极简 Markdown 行扫描（标题/围栏/表格/列表/链接/HTML 注释），
// 覆盖本库语料的全部语法；语料引入本扫描器不认的语法时，先扩展这里再写文档。

import { createHash } from "node:crypto";
import { basename } from "node:path";

/** 语料根：这些树下的 markdown 是双语语料（归档树除外）。 */
const SCOPE_ROOTS = [".agents/notes/", ".agents/flow/", "docs/"];

/** 语料发现时跳过的非源码目录段。 */
const NON_SOURCE_SEGMENTS = new Set(["node_modules", "dist"]);

/**
 * 一条路径是否属于双语语料（base/.en/.i18n.yaml 三种工件都算）。
 * @param {string} file 仓库相对路径（/ 分隔）。
 */
export function isScopeFile(file) {
  if (file === "README.md" || file === "README.en.md" || file === "README.i18n.yaml")
    return true;
  if (file.startsWith(".agents/notes/archived/")) return false; // 冻结件由归档门禁 verify-archived-agent-notes 管辖（sha256 冻结强于配对）
  if (!SCOPE_ROOTS.some((root) => file.startsWith(root))) return false;
  return !file.split("/").some((segment) => NON_SOURCE_SEGMENTS.has(segment));
}

/**
 * 三件套路径：base（中文源）→ { base, en, meta }。
 * @param {string} base 仓库相对的 `foo.md` 路径。
 */
export function pairPaths(base) {
  if (!base.endsWith(".md") || base.endsWith(".en.md"))
    throw new Error(`期望中文 base 路径（foo.md），收到 ${JSON.stringify(base)}`);
  return {
    base,
    en: base.replace(/\.md$/, ".en.md"),
    meta: base.replace(/\.md$/, ".i18n.yaml"),
  };
}

/** CLI 参数归一到 base 锚点：三件套任一文件名或裸词干都指同一对。 */
export function pairAnchorOfArgument(argument) {
  const normalized = argument.replace(/\\/g, "/").replace(/^\.\//, "");
  if (normalized.endsWith(".en.md")) return `${normalized.slice(0, -".en.md".length)}.md`;
  if (normalized.endsWith(".i18n.yaml")) return `${normalized.slice(0, -".i18n.yaml".length)}.md`;
  if (normalized.endsWith(".md")) return normalized;
  return `${normalized}.md`;
}

/**
 * 文件内容的 git blob hash（`git hash-object` 的输出）。
 * 内容寻址：未提交的工作区内容同样可算，本地即时可校验。
 * @param {Buffer} content 精确文件字节。
 */
export function blobHash(content) {
  const body = typeof content === "string" ? Buffer.from(content, "utf8") : content;
  const hash = createHash("sha1");
  hash.update(`blob ${body.byteLength}\0`);
  hash.update(body);
  return hash.digest("hex");
}

// ── 生成区 ───────────────────────────────────────────────────────────────────
// 完整标记行：`<!-- BEGIN GENERATED <slug> … -->` / `<!-- END GENERATED <slug> -->`。
export const REGION_BEGIN = /^<!-- BEGIN GENERATED (\S+)(?: [^>]*)? -->$/;
export const REGION_END = /^<!-- END GENERATED (\S+) -->$/;
const REGION_HINT = /^<!-- (?:BEGIN|END) GENERATED /;

/**
 * 切分生成区（标记行含在内）与人工内容。
 * @throws 未闭合/未开即关/嵌套/标记残缺/slug 不匹配时抛错。
 */
export function partitionGeneratedRegions(content) {
  const regions = [];
  const kept = [];
  let open = null;
  for (const line of content.split("\n")) {
    const begin = REGION_BEGIN.exec(line);
    if (begin?.[1]) {
      if (open) throw new Error("生成区 BEGIN 嵌套在未闭合区域内");
      open = { slug: begin[1], lines: [line] };
      continue;
    }
    const end = REGION_END.exec(line);
    if (end?.[1]) {
      if (!open) throw new Error("生成区 END 没有对应 BEGIN");
      if (end[1] !== open.slug)
        throw new Error(`生成区 END slug "${end[1]}" 与 BEGIN "${open.slug}" 不匹配`);
      open.lines.push(line);
      regions.push(open.lines.join("\n"));
      open = null;
      continue;
    }
    if (REGION_HINT.test(line)) throw new Error(`残缺的生成区标记行：${JSON.stringify(line)}`);
    if (open) open.lines.push(line);
    else kept.push(line);
  }
  if (open) throw new Error("生成区 BEGIN 没有对应 END");
  return { regions, stripped: kept.join("\n") };
}

/** 生成区比对前，把配对文档路径归一到 base 侧（.en.md → .md），其余字节不动。 */
function normalizeRegionLocalePaths(region) {
  return region.replace(/(\S+)\.en\.md/g, "$1.md");
}

/** 两侧生成区是否一致（除 locale 投影的文档路径外逐字节相同）。 */
export function generatedRegionsEqual(baseRegions, enRegions) {
  return (
    baseRegions.length === enRegions.length &&
    baseRegions.every(
      (region, i) => normalizeRegionLocalePaths(region) === normalizeRegionLocalePaths(enRegions[i]),
    )
  );
}

// ── 结构签名 ─────────────────────────────────────────────────────────────────

/** 语言切换行（H1 后第一个非空行）：base 侧与 en 侧两种形态。 */
const SWITCHER_BASE_RE = /^\[English\]\([^)]+\) \| 中文$/;
const SWITCHER_EN_RE = /^English \| \[中文\]\([^)]+\)$/;

/** @param {string} line */
export function isSwitcherLine(line) {
  return SWITCHER_BASE_RE.test(line) || SWITCHER_EN_RE.test(line);
}

/** 行内链接（排除图片）；目标含 query/fragment 原样保留。 */
const LINK_RE = /(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
const HEADING_RE = /^\s{0,3}(#{1,6})(?:\s|$)/;
const LIST_MARKER_RE = /^(\s*)([-*+]|\d{1,9}[.)])(?:\s|$)/;
export const FENCE_OPEN_RE = /^\s{0,3}(`{3,}|~{3,})(.*)$/;
const HTML_COMMENT_START = /^\s*<!--/;

/**
 * 签名里链接目标的归一：语料内 `.en.md` 后缀折算成 base 形态后比较。
 * md 目标的 fragment 是标题的 locale 投影（标题两侧各自本地化），不进镜像，
 * 由死锚门禁按侧校验；query 与非 md 目标（如代码行号）的 query/fragment
 * 逐字保留进镜像。
 * @param {string} target
 */
export function normalizeLinkTarget(target) {
  const boundary = target.search(/[?#]/);
  const path = boundary === -1 ? target : target.slice(0, boundary);
  const suffix = boundary === -1 ? "" : target.slice(boundary);
  const folded = path.endsWith(".en.md") ? `${path.slice(0, -".en.md".length)}.md` : path;
  if (!folded.endsWith(".md")) return folded + suffix;
  const hashAt = suffix.indexOf("#");
  return hashAt === -1 ? folded + suffix : folded + suffix.slice(0, hashAt);
}

/**
 * 提取结构签名：标题层级序列、围栏（info 串 + 内容逐字）、表格行列数、
 * 列表（种类/有序起点/条数）、链接目标序列。切换行的链接不进签名；
 * 语料内 `.en.md` 链接目标归一到 base 形态后比较。
 * 两侧签名相同 ⇔ 结构镜像。
 * @param {string} text 完整 Markdown 文本。
 */
export function parseSignature(text) {
  const lines = text.split("\n");
  const headings = [];
  const code = [];
  const tables = [];
  const linkTargets = [];

  let fence = null; // { char, length, info, body }
  let inComment = false; // 跨行 HTML 注释
  let table = null; // { rows, cols }
  const lists = []; // 全部列表节点，按开始顺序
  let stack = []; // 打开中的列表节点
  const closeWhile = (indent) => {
    while (stack.length && stack[stack.length - 1].level >= indent) stack.pop();
  };
  const openList = (level, marker) => {
    const ordered = /\d/.test(marker[2][0]);
    const node = {
      level,
      ordered,
      start: ordered ? Number(marker[2].replace(/[.)]/, "")) : null,
      items: 1,
    };
    lists.push(node);
    stack.push(node);
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    if (fence) {
      const close = /^\s{0,3}(`{3,}|~{3,})\s*$/.exec(line);
      if (close && close[1][0] === fence.char && close[1].length >= fence.length) {
        code.push(`\`\`\`${fence.info}\n${fence.body.join("\n")}`);
        fence = null;
      } else fence.body.push(line);
      continue;
    }
    if (inComment) {
      if (line.includes("-->")) inComment = false;
      continue;
    }
    const open = FENCE_OPEN_RE.exec(line);
    if (open) {
      fence = { char: open[1][0], length: open[1].length, info: open[2].trim(), body: [] };
      closeWhile(0);
      table = null;
      continue;
    }
    if (HTML_COMMENT_START.test(line)) {
      if (!line.includes("-->")) inComment = true;
      continue; // 注释行不贡献结构（生成区标记也在此列）
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      headings.push(heading[1].length);
      closeWhile(0);
      table = null;
      continue;
    }

    // GFM 表格：当前行含未转义 |，且下一行是分隔行（分隔行本身不计行数）。
    if (line.includes("|") && isDelimiterRow(lines[i + 1] ?? "")) {
      closeWhile(0);
      table = { rows: 1, cols: splitRow(line).length, expectDelimiter: true };
      continue;
    }
    if (table) {
      if (table.expectDelimiter && isDelimiterRow(line)) {
        table.expectDelimiter = false;
        continue;
      }
      if (line.trim() === "" || !line.includes("|")) tables.push(`${table.rows}x${table.cols}`), (table = null);
      else table.rows += 1;
      continue;
    }

    const marker = LIST_MARKER_RE.exec(line);
    if (marker) {
      const level = marker[1].length;
      while (stack.length && stack[stack.length - 1].level > level) stack.pop();
      const top = stack[stack.length - 1];
      const ordered = /\d/.test(marker[2][0]);
      if (top && top.level === level && top.ordered === ordered) top.items += 1;
      else {
        if (top && top.level === level) stack.pop();
        openList(level, marker);
      }
      continue;
    }
    // 非列表非空行：缩进浅于等于活动列表层级则关闭之；更深则是条目续行。
    if (line.trim() !== "") closeWhile((line.match(/^\s*/) ?? [""])[0].length);
  }
  if (fence) throw new Error("文档末尾有未闭合围栏");

  // 链接收集：切换行（形如 `[English](x) | 中文` 的整行）不进签名——
  // 两侧切换行天然指向不同语言侧，属于唯一豁免的跨语言链接。
  for (let i = 0; i < lines.length; i += 1) {
    if (isSwitcherLine(lines[i])) continue;
    for (const [, target] of lines[i].matchAll(LINK_RE))
      linkTargets.push(normalizeLinkTarget(target));
  }

  return {
    headings,
    code,
    tables,
    lists: lists.map((node) =>
      node.ordered ? `ordered:start=${node.start ?? 1}:items=${node.items}` : `bullet:items=${node.items}`,
    ),
    links: linkTargets,
  };
}

/** 展示用：截断过长的签名元素。 */
function show(value) {
  const text = JSON.stringify(value) ?? String(value);
  return text.length > 72 ? `${text.slice(0, 72)}…` : text;
}

/**
 * 两侧签名的分歧清单（每个结构域报第一处）；空数组 ⇔ 结构镜像。
 * @typedef {{headings:number[],code:string[],tables:string[],lists:string[],links:string[]}} Signature
 * @param {Signature} base
 * @param {Signature} en
 */
export function structureDiff(base, en) {
  const out = [];
  const fields = [
    ["标题层级", base.headings, en.headings],
    ["代码围栏", base.code, en.code],
    ["表格行列", base.tables, en.tables],
    ["列表（种类/起点/条数）", base.lists, en.lists],
    ["链接目标", base.links, en.links],
  ];
  for (const [field, baseValues, enValues] of fields) {
    for (let i = 0; i < Math.max(baseValues.length, enValues.length); i += 1) {
      if (baseValues[i] !== enValues[i]) {
        out.push(`${field} #${i + 1} 两侧分歧：${show(baseValues[i])} vs ${show(enValues[i])}`);
        break;
      }
    }
  }
  return out;
}

// ── 链接目标与锚点 ────────────────────────────────────────────────────────────

/**
 * 解析链接目标：判外链（scheme / 协议相对 / 根绝对）、剥 query、拆 #fragment、
 * percent-decode（坏转义回退原文——%zz 不是谁真想链的目标，让它红在存在性检查）。
 * 纯 `#frag`（同文件锚）的 path 为空串。
 * @param {string} url 链接目标原样（含可选 ?query 与 #fragment）。
 * @returns {{external:boolean, path:string, fragment:string|null}} path 已解码；
 * fragment 已解码，无 fragment 或为空串时为 null。
 */
export function parseLinkTarget(url) {
  if (url.startsWith("//") || url.startsWith("/") || /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(url))
    return { external: true, path: "", fragment: null };
  const hashAt = url.indexOf("#");
  const rawPath = (hashAt === -1 ? url : url.slice(0, hashAt)).replace(/\?.*$/, "");
  const rawFragment = hashAt === -1 ? "" : url.slice(hashAt + 1).replace(/\?.*$/, "");
  const decode = (text) => {
    try {
      return decodeURIComponent(text);
    } catch {
      return text;
    }
  };
  return { external: false, path: decode(rawPath), fragment: rawFragment === "" ? null : decode(rawFragment) };
}

/** GitHub 风格 heading slug：小写；丢字母/数字/下划线/空格/连字符之外的一切；空格→连字符。 */
export function githubSlug(heading) {
  return heading.toLowerCase().replace(/[^\p{L}\p{N}_ -]/gu, "").replaceAll(" ", "-");
}

/**
 * 一份 markdown 暴露的全部锚点：每个标题的 GitHub slug（重复标题按 GitHub 占位
 * 计数得 `-1`、`-2`…；标题里的链接取其可见文字再求 slug）+ 正文里显式的
 * `<a id="…">`。围栏与 HTML 注释里的标题/id 是示例或注释，不贡献锚点。
 * @param {string} text 完整 Markdown 文本。
 * @returns {Set<string>} 可被 #fragment 命中的锚点集合。
 */
export function documentAnchors(text) {
  const anchors = new Set();
  const occurrences = new Map();
  const headingRe = /^ {0,3}#{1,6}\s+(.+?)\s*$/;
  const aIdRe = /<a\s+id="([^"]+)"/g;
  let fence = null;
  let inComment = false;
  for (const line of text.split("\n")) {
    if (fence) {
      const close = new RegExp(`^\\s{0,3}\\${fence.char}{${fence.length},}\\s*$`).exec(line);
      if (close) fence = null;
      continue;
    }
    if (inComment) {
      if (line.includes("-->")) inComment = false;
      continue;
    }
    const open = FENCE_OPEN_RE.exec(line);
    if (open) {
      fence = { char: open[1][0], length: open[1].length };
      continue;
    }
    if (HTML_COMMENT_START.test(line)) {
      if (!line.includes("-->")) inComment = true;
      continue; // 注释行不贡献锚点（生成区标记也在此列）
    }
    const heading = headingRe.exec(line);
    if (heading) {
      const visible = heading[1].replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
      const base = githubSlug(visible);
      let slug = base;
      let bump = occurrences.get(base) ?? 0;
      while (anchors.has(slug)) {
        bump += 1;
        slug = `${base}-${bump}`;
      }
      occurrences.set(base, bump);
      anchors.add(slug);
      continue;
    }
    for (const [, id] of line.matchAll(aIdRe)) anchors.add(id);
  }
  return anchors;
}

// ── 配对记录（foo.i18n.yaml）────────────────────────────────────────────────

const META_LINE = /^([^:#]+\.md): ([0-9a-f]{40})$/;

/**
 * 解析配对记录；格式非法、键重复或不是恰好预期的两个 basename 时返回 undefined。
 * @param {string} content sidecar 全文。
 * @param {{base:string,en:string}} paths 期望的三件套路径。
 */
export function parsePairRecord(content, paths) {
  const hashes = new Map();
  for (const line of content.split("\n")) {
    if (line === "" || line.startsWith("#")) continue;
    const match = META_LINE.exec(line);
    if (!match?.[1] || !match[2] || hashes.has(match[1])) return undefined;
    hashes.set(match[1], match[2]);
  }
  const baseName = paths.base.split("/").pop();
  const enName = paths.en.split("/").pop();
  const baseHash = hashes.get(baseName);
  const enHash = hashes.get(enName);
  if (hashes.size !== 2 || !baseHash || !enHash) return undefined;
  return { baseHash, enHash };
}

/**
 * 渲染配对记录（恰好一个尾随换行）。
 * @param {{base:string,en:string}} paths
 * @param {{baseHash:string,enHash:string}} record
 */
export function renderPairRecord(paths, record) {
  return [
    "# 双语配对一致性记录（docs/i18n/README.md）：两侧在最后一次确认一致状态下的",
    "# git blob hash。两种语言同等权威；编辑任一侧后带上另一侧，然后重录：",
    `#   pnpm run record:i18n -- ${paths.base}`,
    `${basename(paths.base)}: ${record.baseHash}`,
    `${basename(paths.en)}: ${record.enHash}`,
    "",
  ].join("\n");
}

// ── 豁免 manifest ────────────────────────────────────────────────────────────

/**
 * 解析 scripts/translation-pairing.manifest.json（只允许 excluded 字段）。
 * @param {string} content
 */
export function parsePairingManifest(content) {
  const value = JSON.parse(content);
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("translation-pairing.manifest.json: 顶层必须是对象");
  const unsupported = Object.keys(value).filter((key) => key !== "excluded" && key !== "//");
  if (unsupported.length > 0)
    throw new Error(`translation-pairing.manifest.json: 不支持的字段 ${unsupported.join(", ")}；语料内文档一律要求配对`);
  if (!Array.isArray(value.excluded))
    throw new Error("translation-pairing.manifest.json: excluded 必须是字符串数组");
  return { excluded: value.excluded };
}

/**
 * manifest 豁免条目是否覆盖此文件：精确文件，或尾随 `/` 的目录子树。
 * @param {string} file
 * @param {{excluded:string[]}} manifest
 */
export function isManifestExcluded(file, manifest) {
  return manifest.excluded.some((entry) => (entry.endsWith("/") ? file.startsWith(entry) : file === entry));
}

// ── CLI 解析 ─────────────────────────────────────────────────────────────────

/**
 * 解析 verify-translation-pairing 的命令行。
 * `--write` 必须点名确认过的 pair（或显式 `--all`），防止盲目 bless 全库漂移。
 * @param {string[]} argv 脚本名之后的参数。
 */
export function parsePairingCliArgs(argv) {
  const args = argv.filter((a) => a !== "--"); // pnpm run 会把 -- 分隔符原样透传
  const flags = args.filter((a) => a.startsWith("--"));
  const anchors = [...new Set(args.filter((a) => !a.startsWith("--")).map(pairAnchorOfArgument))].sort();
  const unknown = flags.filter((f) => !["--list", "--write", "--all"].includes(f));
  if (unknown.length) throw new Error(`未知参数：${unknown.join(", ")}`);
  const listMode = flags.includes("--list");
  const writeMode = flags.includes("--write");
  const allMode = flags.includes("--all");
  if (listMode && (writeMode || allMode || anchors.length))
    throw new Error("--list 只报告全库状态，不与其他参数同用");
  if (allMode && !writeMode) throw new Error("--all 只属于 --write");
  if (writeMode) {
    if (anchors.length && allMode) throw new Error("--write 要么点名 pair，要么 --all，不能同用");
    if (!anchors.length && !allMode)
      throw new Error("--write 须点名确认过的 pair（三件套任一文件名均可），或 --all 显式全量重录");
    return { mode: "write", scope: allMode ? "corpus" : "pairs", anchors };
  }
  if (listMode) return { mode: "list", scope: "corpus", anchors: [] };
  return { mode: "check", scope: anchors.length ? "pairs" : "corpus", anchors };
}

// ── 链接 locale 规则 ─────────────────────────────────────────────────────────

/** GFM 表格分隔行：仅 |、:、-、空白组成，且至少一个 -。 */
function isDelimiterRow(line) {
  return /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(line) && line.includes("-");
}

/** 按 未转义 | 切表格行；首尾围栏 | 已去除，空单元格保留计数。 */
function splitRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/);
}

/**
 * 遍历文本里的行内链接（带 1 起行号），供 locale 校验复用。
 * @yields {{line:number,target:string}}
 */
export function* markdownLinks(text) {
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i += 1) {
    for (const [, target] of lines[i].matchAll(LINK_RE)) yield { line: i + 1, target };
  }
}

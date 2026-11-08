#!/usr/bin/env node
// Gate: 散文段落一行到底（docs/AGENTS.md「写作规则」的机械执行，蓝本
// verify-md-wrap）。散文段跨物理行即红——列表项折行与引用内段落同理，一段只
// 报起始行。围栏与生成区是机器/代码内容，表格、标题、HTML 注释是结构/标记，
// 均不适用。缩进 4 空格的裸代码块按散文对待：本库代码示例一律走围栏，出现
// 缩进代码块本身就是风格漂移。蓝本用 mdast，本库门禁不引解析依赖，手写行
// 扫描；围栏/生成区标记复用 translation-pairing-lib 的唯一定义。扫描面：根
// README.md / README.en.md / AGENTS.md（CLAUDE.md 是其 symlink，不单列）+
// docs/**/*.md + .agents/notes/**/*.md（中英两侧都查）；archived/ 冻结快照
// 不查——内容已 sha256 封存，改它须解封。

import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { FENCE_OPEN_RE, REGION_BEGIN, REGION_END } from "./translation-pairing-lib.mjs";

const ROOT_FILES = ["README.md", "README.en.md", "AGENTS.md"];
const ROOTS = [path.resolve("docs"), path.resolve(".agents/notes"), path.resolve(".agents/flow")];
const ARCHIVED_PREFIX = path.resolve(".agents/notes/archived") + path.sep;

const HEADING = /^ {0,3}#{1,6}(\s|$)/;
const HR_OR_SETEXT = /^ {0,3}(?:(?:[-=*_]\s*){3,}|=+\s*)$/;
const TABLE = /^ {0,3}\|/;
const LIST = /^ {0,8}(?:[-*+]|\d{1,9}[.)])(?:\s|$)/;
const QUOTE_PREFIX = /^ {0,3}>\s?/;

async function collectMarkdown(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) await collectMarkdown(full, out);
    else if (entry.name.endsWith(".md")) out.push(full);
  }
  return out;
}

/** 单文件扫描：返回 [{line, text}]，line 是段落起始行（1 基）。 */
function scanLines(text) {
  const violations = [];
  const lines = text.split("\n");
  let inFence = false;
  let inRegion = false;
  let inComment = false;
  // 段落跟踪：0 无；>0 待报起始行；<0 本段已报（一段只报一次）。
  // lastText 罩散文与列表项（列表行本身合法，但其折行续行是散文）；
  // lastQuote 单罩引用内部（引用剥前缀后再按列表/散文分类）。
  let lastText = 0;
  let lastQuote = 0;
  const push = (line) => violations.push({ line, text: lines[line - 1].trim() });

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const no = i + 1;
    if (inComment) {
      if (line.includes("-->")) inComment = false;
      continue;
    }
    if (line.includes("<!--") && !line.includes("-->")) {
      inComment = true;
      continue;
    }
    if (REGION_BEGIN.test(line)) {
      inRegion = true;
      lastText = lastQuote = 0;
      continue;
    }
    if (inRegion) {
      if (REGION_END.test(line)) inRegion = false;
      continue;
    }
    if (FENCE_OPEN_RE.test(line)) {
      inFence = !inFence;
      lastText = lastQuote = 0;
      continue;
    }
    if (inFence) continue;
    const isLineComment = line.trim().startsWith("<!--") && line.includes("-->");
    if (
      !line.trim() ||
      isLineComment ||
      HEADING.test(line) ||
      HR_OR_SETEXT.test(line) ||
      TABLE.test(line)
    ) {
      lastText = lastQuote = 0;
      continue;
    }
    if (QUOTE_PREFIX.test(line)) {
      const inner = line.replace(QUOTE_PREFIX, "");
      if (!inner.trim() || LIST.test(inner)) {
        lastQuote = 0;
      } else if (lastQuote > 0) {
        push(lastQuote);
        lastQuote = -lastQuote;
      } else if (lastQuote === 0) {
        lastQuote = no;
      }
      lastText = no; // 引用行承载文本：其后裸续行 = 折行
      continue;
    }
    if (LIST.test(line)) {
      lastText = no;
      lastQuote = 0;
      continue;
    }
    if (lastText > 0) {
      push(lastText);
      lastText = -lastText;
    } else if (lastText === 0) {
      lastText = no;
    }
  }
  return violations;
}

export default async function verifyMdWrap() {
  const errors = [];
  const files = [];
  for (const rel of ROOT_FILES) {
    const full = path.resolve(rel);
    if (existsSync(full)) files.push(full);
  }
  for (const root of ROOTS) await collectMarkdown(root, files);

  for (const file of files) {
    if (file.startsWith(ARCHIVED_PREFIX)) continue;
    const rel = path.relative("", file);
    for (const v of scanLines(await readFile(file, "utf8")))
      errors.push(`${rel}:${v.line} 段落硬换行 → ${v.text.slice(0, 80)}`);
  }
  return { ok: errors.length === 0, errors };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const result = await verifyMdWrap();
  if (!result.ok) {
    console.error(`verify-md-wrap: FAIL\n  ${result.errors.join("\n  ")}`);
    process.exit(1);
  }
  console.log("verify-md-wrap: ok");
}

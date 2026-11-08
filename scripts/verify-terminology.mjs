#!/usr/bin/env node
// Gate: 翻译供给链机械背书（契约 home：docs/i18n/README.md）——
// ① 术语表数据行两侧逐字一致（术语对是语言中立数据，表头才本地化）；
// ② 表内「禁用替写」出现在英文侧活语料的散文中即红（围栏/行内代码/
//    生成区是示例或机器写的，不算数；base 侧不查；术语表自身豁免——
//    数据行本来就要写禁用词）；
// ③ 翻译提示词的模板围栏里占位符集合精确：已知占位符各恰好一次，
//    未知占位符即红。全库运行，无参。

import { readFile } from "node:fs/promises";
import { existsSync, readdirSync } from "node:fs";
import { FENCE_OPEN_RE, REGION_BEGIN, REGION_END } from "./translation-pairing-lib.mjs";

const TERMINOLOGY = "docs/i18n/terminology.md";
const TERMINOLOGY_EN = "docs/i18n/terminology.en.md";
const PROMPT = "docs/i18n/translation-prompt.md";
const PLACEHOLDERS = ["source_basename", "terminology", "source_document"];
const PLACEHOLDER_RE = /\{\{([a-z_]+)\}\}/g;

/** 提取术语表数据行（表头与分隔行之后；每行须恰三列）。 */
function terminologyRows(text) {
  const lines = text.split("\n").filter((line) => line.trim().startsWith("|"));
  if (lines.length < 3) throw new Error("表格至少要表头 + 分隔行 + 一条数据行");
  const [, , ...rows] = lines;
  for (const row of rows) {
    const cells = splitRow(row);
    if (cells.length !== 3) throw new Error(`数据行须三列（中文 | English | 禁用替写）→ ${row.trim()}`);
  }
  return rows;
}

function splitRow(row) {
  return row.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

/** 数据行 → 术语条目；禁用替写按中英逗号/顿号切分。 */
function parseTerms(rows) {
  return rows.map((row) => {
    const [zh, en, banned] = splitRow(row);
    return { zh, en, banned: banned ? banned.split(/[,，、]\s*/).filter(Boolean) : [] };
  });
}

/** 发现英文侧活语料（archived/ 冻结件退出检查）。 */
function enCorpus() {
  const files = ["README.en.md"].filter((f) => existsSync(f));
  const walk = (dir) => {
    if (!existsSync(dir)) return;
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const rel = `${dir}/${entry.name}`;
      if (entry.isDirectory()) {
        if (entry.name === "archived" && dir === ".agents/notes") continue;
        walk(rel);
      } else if (entry.name.endsWith(".en.md")) files.push(rel);
    }
  };
  walk("docs");
  walk(".agents/notes");
  walk(".agents/flow");
  return files.filter((f) => f !== TERMINOLOGY_EN).sort();
}

/** 逐行扫禁用替写：跳过生成区/围栏/行内代码（行号按原文保留）。 */
function bannedHits(file, lines, terms) {
  const hits = [];
  let inFence = false;
  let inRegion = false;
  lines.forEach((line, i) => {
    if (REGION_BEGIN.test(line)) {
      inRegion = true;
      return;
    }
    if (REGION_END.test(line)) {
      inRegion = false;
      return;
    }
    if (inRegion) return;
    if (FENCE_OPEN_RE.test(line)) {
      inFence = !inFence;
      return;
    }
    if (inFence) return;
    const prose = line.replace(/`[^`\n]*`/g, " "); // 行内代码是示例
    for (const { zh, en, banned } of terms)
      for (const alt of banned)
        if (new RegExp(`\\b${alt.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(prose))
          hits.push(`${file}:${i + 1} 术语违规 → ${alt}（${zh} 的禁用替写，用 ${en}；见 ${TERMINOLOGY}）`);
  });
  return hits;
}

/** 模板围栏内占位符集合：已知各恰一次、未知即错。 */
function placeholderViolations(text) {
  const fences = [];
  let current = null;
  for (const line of text.split("\n")) {
    if (current !== null) {
      if (FENCE_OPEN_RE.test(line)) fences.push(current.join("\n"));
      else current.push(line);
    } else if (FENCE_OPEN_RE.test(line)) current = [];
  }
  const found = [...fences.join("\n").matchAll(PLACEHOLDER_RE)].map((m) => m[1]);
  const errors = [];
  for (const name of PLACEHOLDERS)
    if (found.filter((f) => f === name).length !== 1)
      errors.push(`${PROMPT}: 模板围栏须恰好一次 {{${name}}}（现 ${found.filter((f) => f === name).length} 次）`);
  for (const name of found.filter((f) => !PLACEHOLDERS.includes(f)))
    errors.push(`${PROMPT}: 未知占位符 {{${name}}}（已知：${PLACEHOLDERS.map((p) => `{{${p}}}`).join(" ")}）`);
  return errors;
}

/**
 * 检查术语表一致性、英文侧术语执行与提示词模板完整性。
 * @returns {{ok: boolean, errors: string[]}}
 */
export default async function verifyTerminology() {
  const errors = [];
  for (const file of [TERMINOLOGY, TERMINOLOGY_EN, PROMPT])
    if (!existsSync(file)) errors.push(`${file}: 翻译供给链缺件（供给链契约见 docs/i18n/README.md）`);
  if (errors.length) return { ok: false, errors };

  let terms;
  let baseRows;
  try {
    baseRows = terminologyRows(await readFile(TERMINOLOGY, "utf8"));
    const enRows = terminologyRows(await readFile(TERMINOLOGY_EN, "utf8"));
    if (baseRows.join("\n") !== enRows.join("\n"))
      errors.push(`${TERMINOLOGY} ↔ ${TERMINOLOGY_EN}: 数据行不一致（术语对是语言中立数据，只本地化表头）`);
    terms = parseTerms(baseRows);
  } catch (error) {
    errors.push(`${TERMINOLOGY}: ${error instanceof Error ? error.message : String(error)}`);
    return { ok: false, errors };
  }

  for (const file of enCorpus())
    errors.push(...bannedHits(file, (await readFile(file, "utf8")).split("\n"), terms));

  errors.push(...placeholderViolations(await readFile(PROMPT, "utf8")));
  return { ok: errors.length === 0, errors };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { ok, errors } = await verifyTerminology();
  if (!ok) {
    console.error(`verify-terminology: FAIL\n  ${errors.join("\n  ")}`);
    process.exit(1);
  }
  console.log("verify-terminology: ok");
}

#!/usr/bin/env node
// Gate: docs 索引完整性。docs/README.md 的地图表是唯一索引：
// 每个 docs/**/*.md（postmortem 事故件除外）必须登记，登记的必须存在。

import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

const DOCS_ROOT = path.resolve("docs");
const INDEX = path.join(DOCS_ROOT, "README.md");

async function collectMarkdown(dir, out = []) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) await collectMarkdown(full, out);
    else if (entry.name.endsWith(".md")) out.push(full);
  }
  return out;
}

export default async function verifyDocIndex() {
  const errors = [];
  if (!existsSync(INDEX)) return { ok: false, errors: ["docs/README.md 索引不存在"] };

  const indexText = await readFile(INDEX, "utf8");
  // 只认地图表里的相对 .md 链接（表格行以 | 开头）
  const registered = new Set();
  for (const line of indexText.split("\n")) {
    if (!line.trimStart().startsWith("|")) continue;
    for (const [, target] of line.matchAll(/\]\(([^)]+\.md)\)/g)) {
      registered.add(path.normalize(path.join(DOCS_ROOT, target)));
    }
  }

  const files = await collectMarkdown(DOCS_ROOT);
  const mustRegister = files.filter((f) => {
    const rel = path.relative(DOCS_ROOT, f);
    if (f === INDEX) return false; // 索引自身是登记处，不登记
    if (rel.startsWith(`postmortem${path.sep}`) && /^\d{4}-/.test(path.basename(rel)))
      return false; // 事故件按编号浏览，不进索引
    if (rel.endsWith(".en.md")) return false; // 英文侧随 base 侧登记，由配对门禁管辖
    return true;
  });

  for (const file of mustRegister) {
    if (!registered.has(path.normalize(file)))
      errors.push(`${path.relative("", file)}: 未登记进 docs/README.md 地图表`);
  }
  for (const target of registered) {
    if (!existsSync(target))
      errors.push(`docs/README.md: 登记的 ${path.relative("", target)} 不存在`);
  }

  return { ok: errors.length === 0, errors };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const result = await verifyDocIndex();
  if (!result.ok) {
    console.error(`verify-doc-index: FAIL\n  ${result.errors.join("\n  ")}`);
    process.exit(1);
  }
  console.log("verify-doc-index: ok");
}

#!/usr/bin/env node
// Gate: 常驻文档词数预算（manifest 是唯一权威）。计数规则：CJK 字符按字计，
// 其余按空白分词——中英混排下两侧权重一致；生成区是机器写的，不计数。
// 处置顺序：搬层 → 压缩 → 提预算（PR 里说明理由）。预算是护栏不是瘦身目标。

import { readFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { partitionGeneratedRegions } from "./translation-pairing-lib.mjs";

const MANIFEST = "scripts/doc-budgets.manifest.json";

export function countWords(text) {
  const cjk = (text.match(/[㐀-鿿豈-﫿぀-ヿ]/g) ?? []).length;
  const stripped = text.replace(/[㐀-鿿豈-﫿぀-ヿ]/g, " ");
  const words = stripped.split(/\s+/).filter(Boolean).length;
  return cjk + words;
}

/** 只数人写的部分：生成区整体剔除。 */
function authoredWords(text) {
  return countWords(partitionGeneratedRegions(text).stripped);
}

/** 发现必须登记预算的常驻文档（存在的 base 侧）：docs/**\/*.md、根
 *  README.md、AGENTS.md，以及 .agents/flow/ 的 README/roadmap/milestones。
 *  issues 随讨论增长，.en.md 由 base 侧代表，两者都不单独登记。 */
async function residentDocs() {
  const files = new Set(
    ["README.md", "AGENTS.md", ".agents/flow/README.md", ".agents/flow/roadmap.md"].filter((f) => existsSync(f)),
  );
  const flowMilestones = ".agents/flow/milestones";
  if (existsSync(flowMilestones))
    for (const entry of await readdir(flowMilestones))
      if (entry.endsWith(".md") && !entry.endsWith(".en.md"))
        files.add(`${flowMilestones}/${entry}`);
  const walk = async (dir) => {
    if (!existsSync(dir)) return;
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const rel = `${dir}/${entry.name}`;
      if (entry.isDirectory()) await walk(rel);
      else if (entry.name.endsWith(".md") && !entry.name.endsWith(".en.md")) files.add(rel);
    }
  };
  await walk("docs");
  return [...files].sort();
}

export default async function verifyDocBudgets() {
  const errors = [];
  const budgets = JSON.parse(await readFile(MANIFEST, "utf8"));

  for (const [file, ceiling] of Object.entries(budgets)) {
    if (file.startsWith("//")) continue; // "//" 是注释键的约定，不是路径

    const full = path.resolve(file);
    if (!existsSync(full)) {
      errors.push(`${file}: 预算登记了但文件不存在（删除登记或恢复文件）`);
      continue;
    }
    const text = await readFile(full, "utf8");
    const count = authoredWords(text);
    if (count > ceiling)
      errors.push(`${file}: ${count} 词超预算 ${ceiling}（先搬层/压缩，提预算须 PR 说明）`);
  }

  // 反向对账：只查 manifest 内条目会令"未登记即免检"成为绕过预算的漏洞。
  const registered = new Set(Object.keys(budgets).filter((key) => !key.startsWith("//")));
  for (const file of await residentDocs())
    if (!registered.has(file))
      errors.push(`${file}: 常驻文档未登记预算（scripts/doc-budgets.manifest.json），登记即受检`);
  return { ok: errors.length === 0, errors };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const result = await verifyDocBudgets();
  if (!result.ok) {
    console.error(`verify-doc-budgets: FAIL\n  ${result.errors.join("\n  ")}`);
    process.exit(1);
  }
  console.log("verify-doc-budgets: ok");
}

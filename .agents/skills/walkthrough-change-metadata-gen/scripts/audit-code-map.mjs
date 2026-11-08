#!/usr/bin/env node
// 变更页逐段注解的机械审计：拦截重复说明、纯标点锚点和多重命中。
// 它不判断语义是否讲对，但能发现“批量套模板后按位置拼接”的常见失败形态。

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { locateSnippet } from "../../../../scripts/walkthrough-lib.mjs";
import { parseWalkthroughYaml } from "../../../../scripts/walkthrough-yaml.mjs";

const relativePage = process.argv[2];
const root = path.resolve(process.argv[3] ?? process.cwd());

if (!relativePage) {
  console.error("用法: node audit-code-map.mjs <walkthrough/changes/issue.yaml> [repo-root]");
  process.exit(2);
}

/** 判断一段文本是否只剩标点或注释外壳，无法独立表达代码行为。 */
function lacksSemanticAnchor(snippet) {
  const meaningful = snippet
    .replace(/\/\*[\s\S]*?\*\//gu, "")
    .replace(/\/\/.*$/gmu, "")
    .replace(/[^\p{L}\p{N}_$]+/gu, "");
  return meaningful === "";
}

let page;
try {
  const absolute = path.resolve(root, relativePage);
  page = parseWalkthroughYaml(readFileSync(absolute, "utf8"), relativePage);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(2);
}

const errors = [];
for (const group of page.codeMap ?? []) {
  for (const file of group.files ?? []) {
    const changes = file.changes ?? [];
    if (changes.length === 0) continue;

    const notes = new Map();
    const sourcePath = path.resolve(root, file.path);
    const source = existsSync(sourcePath) ? readFileSync(sourcePath, "utf8") : null;

    changes.forEach((change, index) => {
      const label = `${file.path} changes[${index + 1}]`;
      const snippet = typeof change?.snippet === "string" ? change.snippet : "";
      const note = typeof change?.note === "string" ? change.note.trim() : "";

      if (lacksSemanticAnchor(snippet)) {
        errors.push(`${label}: snippet 只有括号或注释外壳，需带上可识别的代码行为`);
      }
      if (note) {
        if (notes.has(note)) {
          errors.push(`${label}: note 与 changes[${notes.get(note)}] 重复`);
        } else {
          notes.set(note, index + 1);
        }
      }
      if (source && snippet.trim()) {
        const located = locateSnippet(source, snippet);
        if (!located) {
          errors.push(`${label}: snippet 无法在源码中定位`);
        } else if (located.count > 1) {
          errors.push(`${label}: snippet 命中 ${located.count} 处，需扩展上下文直到唯一`);
        }
      }
    });
  }
}

if (errors.length > 0) {
  console.error("audit-code-map: FAIL");
  for (const error of errors) console.error(`  ${error}`);
  process.exit(1);
}

console.log("audit-code-map: ok");

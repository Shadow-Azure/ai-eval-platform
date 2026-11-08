#!/usr/bin/env node
// Gate: 源码文档引用防腐——代码里的 docs/**\/*.md 与 .agents/notes/**\/*.md
// 路径引用（注释或字符串），目标必须存在；文档改名/删除而漏改源码即红。
// 行内代码与围栏里的是示例（如用法行的 `docs/foo.md`），不算数，与其余
// 链接门禁口径一致。扫描面：scripts/*.mjs（*.spec.mjs 除外——夹具全是
// 合成路径）、src/**/*.ts、src-tauri/src/**/*.rs。全库运行，无参。

import { readdirSync, readFileSync, existsSync } from "node:fs";

const DOC_REF_RE = /(?:\bdocs|\.agents\/notes)\/[\p{L}\p{N}._/-]+\.md/gu;

// 源码注释里的围栏带注释前缀（如 "// ```md"）；剥掉单行注释标记再判围栏，
// 否则"围栏里是示例"的口径对注释内围栏不成立。
const FENCE_IN_COMMENT_RE = /^\s*(?:\/\/+|#|\*)?\s*(`{3,}|~{3,})(.*)$/;

const SCAN_SPEC = [
  { root: "scripts", ext: ".mjs", exclude: (name) => name.endsWith(".spec.mjs") },
  { root: "src", ext: ".ts" },
  { root: "src-tauri/src", ext: ".rs" },
];

/** 收集一个目录树下指定扩展名的文件（仓库相对路径）。 */
function collectFiles(dir, ext, exclude, out = []) {
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = `${dir}/${entry.name}`;
    if (entry.isDirectory()) collectFiles(full, ext, exclude, out);
    else if (entry.name.endsWith(ext) && !exclude?.(entry.name)) out.push(full);
  }
  return out;
}

/**
 * 扫描三类源码里的文档路径引用，目标不存在即报错。
 * @returns {{ok: boolean, errors: string[]}}
 */
export default function verifyDocRefs() {
  const errors = [];
  for (const { root, ext, exclude } of SCAN_SPEC) {
    for (const file of collectFiles(root, ext, exclude)) {
      const lines = readFileSync(file, "utf8").split("\n");
      let inFence = false;
      lines.forEach((line, i) => {
        if (FENCE_IN_COMMENT_RE.test(line)) {
          inFence = !inFence;
          return;
        }
        if (inFence) return;
        const prose = line.replace(/`[^`\n]*`/g, " "); // 行内代码是示例
        for (const match of prose.matchAll(DOC_REF_RE)) {
          const ref = match[0];
          if (!existsSync(ref)) errors.push(`${file}:${i + 1} 引用不存在的文档 → ${ref}`);
        }
      });
    }
  }
  return { ok: errors.length === 0, errors };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { ok, errors } = verifyDocRefs();
  if (!ok) {
    console.error(`verify-doc-refs: FAIL\n  ${errors.join("\n  ")}`);
    process.exit(1);
  }
  console.log("verify-doc-refs: ok");
}

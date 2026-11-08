#!/usr/bin/env node
// Gate: 相对 markdown 链接可达（死链检查）+ markdown 目标上的 #fragment 命中
// 真实标题 slug 或显式 <a id>（死锚检查；蓝本 verify-md-links 的移植，手写行
// 扫描不引解析依赖，锚点推导复用 translation-pairing-lib 的唯一定义）。
// 扫描面：根 README.md / README.en.md / AGENTS.md（CLAUDE.md 是 AGENTS.md 的
// symlink，不单列）+ docs/**/*.md + .agents/notes/**/*.md（中英两侧都查）。
// 外链（http/https 等）不在范围：本库无站点投影，外链不做机械检查（引入投影时收编）。
// archived/ 冻结快照不查——其中的链接是历史事实，改它须解封。

import { readdir, readFile } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { documentAnchors, parseLinkTarget } from "./translation-pairing-lib.mjs";

const ROOT_FILES = ["README.md", "README.en.md", "AGENTS.md"];
const ROOTS = [path.resolve("docs"), path.resolve(".agents/notes"), path.resolve(".agents/flow")];
const ARCHIVED_PREFIX = path.resolve(".agents/notes/archived") + path.sep;

async function collectMarkdown(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) await collectMarkdown(full, out);
    else if (entry.name.endsWith(".md")) out.push(full);
  }
  return out;
}

/** 目标文件的锚点集（多链接指向同一目标只解析一次）。 */
const anchorMemo = new Map();
function anchorsOf(absPath) {
  if (!anchorMemo.has(absPath))
    anchorMemo.set(absPath, documentAnchors(readFileSync(absPath, "utf8")));
  return anchorMemo.get(absPath);
}

export default async function verifyMdLinks() {
  const errors = [];
  const linkRe = /\]\(([^)\s]+)\)/g;

  const files = [];
  for (const rel of ROOT_FILES) {
    const full = path.resolve(rel);
    if (existsSync(full)) files.push(full);
  }
  for (const root of ROOTS) await collectMarkdown(root, files);

  for (const file of files) {
    if (file.startsWith(ARCHIVED_PREFIX)) continue;
    const rel = path.relative("", file);
    const text = await readFile(file, "utf8");
    // 只查正文：围栏代码与行内代码里的"链接"是示例/代码，不是链接。
    const prose = text
      .replace(/```[\s\S]*?(?:```|$)/g, " ")
      .replace(/`[^`\n]*`/g, " ");
    for (const [, target] of prose.matchAll(linkRe)) {
      const { external, path: targetPath, fragment } = parseLinkTarget(target);
      if (external) continue;
      // 纯 #frag 是同文件锚：目标就是链接所在文件。
      const resolved = targetPath ? path.resolve(path.dirname(file), targetPath) : file;
      if (!existsSync(resolved)) {
        errors.push(`${rel}: 死链 → ${target}`);
        continue;
      }
      // 非 markdown 目标（如 x.mjs#L3）的 fragment 是行号等定位符，不按锚点校验。
      if (fragment && resolved.endsWith(".md") && !anchorsOf(resolved).has(fragment))
        errors.push(`${rel}: 死锚 → ${target}`);
    }
  }
  return { ok: errors.length === 0, errors };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const result = await verifyMdLinks();
  if (!result.ok) {
    console.error(`verify-md-links: FAIL\n  ${result.errors.join("\n  ")}`);
    process.exit(1);
  }
  console.log("verify-md-links: ok");
}

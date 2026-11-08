#!/usr/bin/env node
// 脚手架：新建 flow issue 三件套（中文 base + 英文侧 + 配对记录），状态
// backlog、github 待回填。用法：
//   pnpm flow:new-issue -- --milestone m1 --priority P0 --slug excel-viewer \
//     --title 中文标题 --title-en "English title" --scope "src/**,docs/**"
// 生成后补正文（背景/目标/验收，双侧最小修补），流转 ready 前须 flow:sync 回填编号。

import { mkdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { blobHash, pairPaths, renderPairRecord } from "./translation-pairing-lib.mjs";
import { ISSUE_STATUSES, PRIORITIES, loadFlowTree } from "./flow-lib.mjs";

const FENCE = (milestone, priority, scopes) => [
  "```yaml flow",
  "kind: issue",
  `milestone: ${milestone}`,
  `priority: ${priority}`,
  "status: backlog",
  "scope:",
  ...scopes.map((s) => `  - ${s}`),
  "adr: []",
  "github:",
  "  number: null",
  "  url: null",
  "```",
].join("\n");

/** 生成 issue 三件套。返回三件套路径。 */
export async function createIssue({ root = ".", milestone, priority, slug, title, titleEn, scopes }) {
  if (!milestone || !slug || !title || !titleEn || !Array.isArray(scopes) || scopes.length === 0)
    throw new Error("milestone/slug/title/title-en/scope 均为必填");
  if (!PRIORITIES.includes(priority))
    throw new Error(`priority 必须是 ${PRIORITIES.join(" | ")}`);
  if (!/^[a-z0-9-]+$/.test(slug)) throw new Error(`slug 必须是 kebab-case：${slug}`);
  const { tree, errors } = await loadFlowTree(path.join(root, ".agents/flow"));
  if (errors.length > 0) throw new Error(`流程树有错误：${errors.join("；")}`);
  if (!tree.milestones.some((m) => m.data.id === milestone))
    throw new Error(`milestone ${milestone} 不存在于 roadmap 序列`);

  const name = `${milestone}-${slug}`;
  const base = path.join(root, ".agents/flow/issues", `${name}.md`);
  if (existsSync(base)) throw new Error(`issue 已存在：${base}`);
  const fence = FENCE(milestone, priority, scopes);
  const zh = `# ${title}

[English](${name}.en.md) | 中文

${fence}

## 背景

<待补：为什么做>

## 目标

- <待补>

## 验收

- <待补>
`;
  const en = `# ${titleEn}

English | [中文](${name}.md)

${fence}

## Background

<to fill: why>

## Goals

- <to fill>

## Acceptance

- <to fill>
`;
  const paths = pairPaths(base);
  await mkdir(path.dirname(base), { recursive: true });
  await writeFile(paths.base, zh);
  await writeFile(paths.en, en);
  await writeFile(
    paths.meta,
    renderPairRecord(paths, { baseHash: blobHash(zh), enHash: blobHash(en) }),
  );
  return paths;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2).filter((a) => a !== "--");
  const read = (flag) => {
    const i = args.indexOf(flag);
    return i >= 0 ? args[i + 1] : undefined;
  };
  try {
    const paths = await createIssue({
      milestone: read("--milestone"),
      priority: read("--priority"),
      slug: read("--slug"),
      title: read("--title"),
      titleEn: read("--title-en"),
      scopes: read("--scope")?.split(",").map((s) => s.trim()).filter(Boolean),
    });
    console.log(`flow:new-issue: 已生成 ${paths.base}（三件套）`);
    console.log("下一步：补背景/目标/验收（双侧），流转前 pnpm flow:sync 回填编号");
  } catch (error) {
    console.error(`flow:new-issue: ${error.message}`);
    process.exit(1);
  }
}

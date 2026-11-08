#!/usr/bin/env node
// Gate: 开发流程体系（roadmap → milestone → issue → ADR）。规则 home：
// .agents/flow/README.md（封闭集合与 flow-lib 互为镜像）。
// 默认模式（离线，进 doc-quick/doc-sync/release）：流程树状态机全量校验。
// --diff <base> 模式（CI on PR）：在离线校验之上追加——base 树无 roadmap 时
// 跳过（流程自举前的 PR 不绑人）；每个 commit 标题与 PR 标题（PR_TITLE 环境
// 变量，CI 必传）须挂 (#N) 引用；引用的 issue 须在树内且处于 ready/
// in-progress；若本 PR 把 backlog issue 推进为 ready/in-progress/done，
// 则视为开工激活；diff 触及的文件须落在所挂 issue 的 scope 并集内。无豁免通道。

import { execFileSync } from "node:child_process";
import {
  collectRefs,
  loadFlowTree,
  parseFlowDocument,
  uncoveredFiles,
  validateTree,
} from "./flow-lib.mjs";

/** 从 origin remote 推导 "owner/repo"；不可得（无 remote/无 git）返回 undefined。 */
function deriveRepo() {
  try {
    const url = execFileSync("git", ["remote", "get-url", "origin"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    const m = /github\.com[:/]([^/]+\/[^/]+?)(?:\.git)?$/.exec(url);
    return m?.[1];
  } catch {
    return undefined;
  }
}

function git(args) {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

/**
 * PR 内引用 issue 的有效状态：优先按 base 评估，避免引用已在 main 上 done
 * 的 issue。base 为 backlog 且 HEAD 已推进为 ready/in-progress/done 时视为
 * 本 PR 完成开工激活。base 无该文件（新 issue 首次提交）返回 undefined，
 * 调用方回落到 HEAD 状态。
 */
function baseIssueStatus(base, file) {
  try {
    const text = execFileSync("git", ["show", `${base}:${file}`], {
      encoding: "utf8",
    });
    return parseFlowDocument(text, file).data.status;
  } catch {
    return undefined;
  }
}

/** base 树是否已启用流程（无 roadmap 则本 PR 是流程自身的自举，跳过 diff 校验）。 */
function baseHasRoadmap(base) {
  try {
    execFileSync("git", ["cat-file", "-e", `${base}:.agents/flow/roadmap.md`], {
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
  }
}

/** 离线模式：流程树状态机。run-gates 叶子的入口。 */
export default async function verifyFlow() {
  const { tree, errors } = await loadFlowTree();
  if (errors.length > 0) return { ok: false, errors };
  const structural = validateTree(tree, { repo: deriveRepo() });
  return { ok: structural.length === 0, errors: structural };
}

/** diff 模式：离线校验 + PR 引用/scope 覆盖。返回 { ok, errors, skipped }。 */
export async function verifyFlowDiff(base) {
  if (!baseHasRoadmap(base))
    return { ok: true, errors: [], skipped: `base ${base} 尚无 .agents/flow/roadmap.md——流程未在 base 启用，跳过 diff 校验` };
  const offline = await verifyFlow();
  const errors = [...offline.errors];

  const subjects = git(["log", "--format=%s", "--no-merges", `${base}..HEAD`]).split("\n").filter(Boolean);
  if (subjects.length === 0) errors.push(`${base}..HEAD 没有 commit`);
  const referenced = new Set();
  for (const subject of subjects) {
    const refs = collectRefs(subject);
    if (refs.length === 0)
      errors.push(`commit 标题缺 issue 引用（须含 (#N)）：${subject}`);
    for (const n of refs) referenced.add(n);
  }
  const prTitle = process.env.PR_TITLE;
  if (prTitle !== undefined) {
    const refs = collectRefs(prTitle);
    if (refs.length === 0) errors.push(`PR 标题缺 issue 引用（须含 (#N)）：${prTitle}`);
    for (const n of refs) referenced.add(n);
  }

  const { tree } = await loadFlowTree();
  const scopes = [];
  for (const n of referenced) {
    const issue = tree.issues.find((i) => i.data.github?.number === n);
    if (!issue) {
      errors.push(`引用的 issue #${n} 在 .agents/flow/issues/ 内不存在（先建 issue 或先跑 pnpm flow:sync 回填）`);
      continue;
    }
    const baseStatus = baseIssueStatus(base, issue.file);
    const headStatus = issue.data.status;
    const activated = baseStatus === "backlog" && ["ready", "in-progress", "done"].includes(headStatus);
    const status = baseStatus ?? headStatus;
    if (!activated && !["ready", "in-progress"].includes(status))
      errors.push(`#${n}（${issue.name}）在 ${base} 的 status 是 ${status}——只有 ready / in-progress 的 issue 能挂提交；本 PR 内从 backlog 激活除外`);
    scopes.push(...(Array.isArray(issue.data.scope) ? issue.data.scope : []));
  }
  if (errors.length === 0 && referenced.size > 0) {
    const changed = git(["diff", "--name-only", `${base}...HEAD`]).split("\n").filter(Boolean);
    const outside = uncoveredFiles(changed, scopes);
    for (const f of outside)
      errors.push(`${f}: 不在所挂 issue 的 scope 并集内（调整改动面，或在 issue 里扩 scope 并说明）`);
  }
  return { ok: errors.length === 0, errors, skipped: undefined };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  const diffIdx = args.indexOf("--diff");
  if (diffIdx >= 0) {
    const base = args[diffIdx + 1];
    if (!base) {
      console.error("verify-flow --diff 需要 <base> 参数（如 origin/main）");
      process.exit(2);
    }
    const result = await verifyFlowDiff(base);
    if (result.skipped) console.log(`verify-flow --diff: ${result.skipped}`);
    if (!result.ok) {
      console.error(`verify-flow --diff: FAIL\n  ${result.errors.join("\n  ")}`);
      process.exit(1);
    }
    console.log("verify-flow --diff: ok");
  } else {
    const result = await verifyFlow();
    if (!result.ok) {
      console.error(`verify-flow: FAIL\n  ${result.errors.join("\n  ")}`);
      process.exit(1);
    }
    console.log("verify-flow: ok");
  }
}

#!/usr/bin/env node
// Gate（CI 在线 lane）：流程树与 GitHub 的双侧一致。只校验不修改——写 GitHub
// 的唯一入口是 pnpm flow:sync。校验：带编号的 issue 存在、状态映射（done ↔
// CLOSED，其余 ↔ OPEN）、归属 milestone 的 title 匹配、url 一致；带编号的
// milestone 同理。PR_NUMBER 环境变量存在时追加：PR 关联了 milestone 与
// project。需要 gh 已认证（CI 用 GITHUB_TOKEN）。规则 home：.agents/flow/README.md。

import { execFileSync } from "node:child_process";
import { loadFlowTree } from "./flow-lib.mjs";

/** issue 的本地 status ↔ GitHub state 映射校验。 */
export function checkIssueRemote(file, localStatus, remote) {
  const errors = [];
  const expected = localStatus === "done" ? "CLOSED" : "OPEN";
  if (remote.state !== expected)
    errors.push(`${file}: 本地 status ${localStatus} 应对应 GitHub ${expected}，实有 ${remote.state}（手工改过一侧？跑 pnpm flow:sync 对齐）`);
  return errors;
}

/** issue 归属 milestone 的 title 匹配。 */
export function checkIssueMilestone(file, expectedTitle, remote) {
  if (!remote.milestone) return [`${file}: GitHub issue 未归属任何 milestone（期望 ${expectedTitle}）`];
  if (remote.milestone.title !== expectedTitle)
    return [`${file}: GitHub milestone 是 ${JSON.stringify(remote.milestone.title)}，本地期望 ${JSON.stringify(expectedTitle)}`];
  return [];
}

/** milestone 的远端一致性：title 匹配、done ↔ closed。 */
export function checkMilestoneRemote(file, local, remote) {
  const errors = [];
  if (remote.title !== local.title)
    errors.push(`${file}: GitHub milestone title 是 ${JSON.stringify(remote.title)}，本地是 ${JSON.stringify(local.title)}`);
  const expected = local.status === "done" ? "closed" : "open";
  if (remote.state !== expected)
    errors.push(`${file}: 本地 status ${local.status} 应对应 GitHub ${expected}，实有 ${remote.state}`);
  return errors;
}

/**
 * PR 关联校验：必须挂 milestone 与 project。
 * `gh pr view --json projectItems` 给数组，GraphQL 给 `{nodes}`——两种形状都认，
 * 否则真挂了 project 的 PR 也会被判成未关联。
 * project 项只有在 token 能读 Projects v2 时才可信（app token 恒见空列表），
 * 调用方用 projectCheck 显式开关；关掉时该项交本地 `pnpm verify:flow-online`。
 * @param {object} pr `gh pr view --json milestone,projectItems` 的结果
 * @param {{projectCheck?: boolean}} [options]
 */
export function checkPrRemote(pr, { projectCheck = true } = {}) {
  const errors = [];
  if (!pr.milestone) errors.push("PR 未关联 milestone（GitHub 侧栏设置）");
  if (projectCheck) {
    const items = pr.projectItems;
    const count = Array.isArray(items) ? items.length : (items?.nodes?.length ?? 0);
    if (count === 0) errors.push("PR 未关联 project（GitHub 侧栏设置）");
  }
  return errors;
}

function deriveRepo() {
  const url = execFileSync("git", ["remote", "get-url", "origin"], { encoding: "utf8" }).trim();
  const m = /github\.com[:/]([^/]+\/[^/]+?)(?:\.git)?$/.exec(url);
  if (!m) throw new Error(`无法从 origin 推导 GitHub 仓库：${url}`);
  return m[1];
}

function gh(args) {
  return JSON.parse(execFileSync("gh", args, { encoding: "utf8" }));
}

async function main() {
  const { tree, errors } = await loadFlowTree();
  if (!tree.roadmap) {
    console.log("verify-flow-online: 流程树未启用，跳过");
    return;
  }
  if (errors.length > 0) {
    console.error(`verify-flow-online: 流程树解析失败\n  ${errors.join("\n  ")}`);
    process.exit(1);
  }
  const repo = deriveRepo();
  const all = [];
  const milestoneTitle = new Map(tree.milestones.map((m) => [m.data.id, m.data.title]));
  for (const m of tree.milestones) {
    const n = m.data.github?.number;
    if (!Number.isInteger(n)) continue;
    const remote = gh(["api", `repos/${repo}/milestones/${n}`]);
    all.push(...checkMilestoneRemote(m.file, m.data, remote));
  }
  for (const issue of tree.issues) {
    const n = issue.data.github?.number;
    if (!Number.isInteger(n)) continue;
    const remote = gh(["issue", "view", String(n), "--repo", repo, "--json", "state,milestone,url"]);
    all.push(...checkIssueRemote(issue.file, issue.data.status, remote));
    all.push(...checkIssueMilestone(issue.file, milestoneTitle.get(issue.data.milestone), remote));
    if (remote.url !== issue.data.github.url)
      all.push(`${issue.file}: github.url 与远端不符（本地 ${issue.data.github.url}，远端 ${remote.url}）`);
  }
  const prNumber = process.env.PR_NUMBER;
  if (prNumber) {
    const pr = gh(["pr", "view", prNumber, "--repo", repo, "--json", "milestone,projectItems"]);
    const projectCheck = process.env.FLOW_PROJECT_CHECK === "1";
    if (!projectCheck)
      console.log("verify-flow-online: 未配 FLOW_TOKEN，project 关联降级为提醒（本地跑本命令用 PAT 强校验）");
    all.push(...checkPrRemote(pr, { projectCheck }));
  }
  if (all.length > 0) {
    console.error(`verify-flow-online: FAIL\n  ${all.join("\n  ")}`);
    process.exit(1);
  }
  console.log("verify-flow-online: ok");
}

if (import.meta.url === `file://${process.argv[1]}`) await main();

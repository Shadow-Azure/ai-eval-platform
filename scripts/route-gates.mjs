#!/usr/bin/env node
// Route changed paths to the smallest useful local verification. Routing is
// advisory; the CI doc-sync lane remains the exhaustive backstop.

import { execFileSync } from "node:child_process";

export const ROUTES = [
  {
    match: (file) => file.startsWith(".agents/flow/"),
    commands: ["test", "verify:docs"],
    reason: "流程文档：自测试、配对与预算；roadmap 创建后再运行 verify:flow",
  },
  {
    match: (file) =>
      file.startsWith("docs/") ||
      file.startsWith(".agents/skills/") ||
      file === "AGENTS.md" ||
      file === "README.md" ||
      file === "README.en.md",
    commands: ["verify:docs"],
    reason: "文档语料：索引、预算、换行、配对与术语",
  },
  {
    match: (file) =>
      file.startsWith("scripts/") ||
      file.startsWith("tests/") ||
      file === "package.json" ||
      file.startsWith(".github/"),
    commands: ["test", "verify:docs"],
    reason: "门禁或 CI 接线：自测试与全量文档校验",
  },
];

export const FALLBACK = {
  commands: ["lint:docs"],
  reason: "未命中路由：快速文档档兜底",
};

export function classify(paths) {
  return ROUTES.filter((route) => paths.some((file) => route.match(file)));
}

const COMMAND_ORDER = ["test", "verify:flow", "verify:docs", "lint:docs"];

export function recommend(paths) {
  const hits = classify(paths);
  const source = hits.length ? hits : [FALLBACK];
  const commands = [...new Set(source.flatMap((hit) => hit.commands))].sort(
    (left, right) => COMMAND_ORDER.indexOf(left) - COMMAND_ORDER.indexOf(right),
  );
  return { commands, hits: source };
}

export function worktreePaths(statusOutput) {
  return [
    ...new Set(
      statusOutput
        .split("\n")
        .filter(Boolean)
        .map((line) => {
          const rename = line.slice(3).match(/^(.*?) -> (.*)$/);
          return rename ? [rename[1], rename[2]] : line.slice(3);
        })
        .flat(),
    ),
  ];
}

function changedPaths() {
  const args = process.argv.slice(2);
  const baseIndex = args.indexOf("--base");
  if (baseIndex === -1) {
    return worktreePaths(
      execFileSync("git", ["status", "--porcelain"], { encoding: "utf8" }),
    );
  }
  const base = args[baseIndex + 1];
  if (!base) {
    console.error("route-gates: --base 需要一个 ref 参数（如 --base main）");
    process.exit(2);
  }
  const committed = execFileSync("git", ["diff", "--name-only", base], {
    encoding: "utf8",
  });
  const untracked = execFileSync("git", ["status", "--porcelain"], {
    encoding: "utf8",
  });
  return [
    ...new Set([
      ...committed.split("\n").filter(Boolean),
      ...worktreePaths(untracked),
    ]),
  ];
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const paths = changedPaths();
  if (!paths.length) {
    console.log("route-gates: 无改动。");
    process.exit(0);
  }
  const { commands, hits } = recommend(paths);
  console.log(`改动面（${paths.length} 条路径）命中：`);
  for (const hit of hits) console.log(`  - ${hit.reason}`);
  console.log("\n建议按序跑：");
  for (const command of commands) console.log(`  npm run ${command}`);
}

import assert from "node:assert/strict";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { execFileSync, spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { createIssue } from "../scripts/flow-new-issue.mjs";
import { runSync } from "../scripts/flow-sync.mjs";
import { installGitHooks } from "../scripts/install-git-hooks.mjs";

const write = (root, relative, contents) => {
  const file = path.join(root, relative);
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, contents);
};

const createTree = (context) => {
  const root = mkdtempSync(path.join(tmpdir(), "ai-eval-flow-tools-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  write(
    root,
    ".agents/flow/roadmap.md",
    `# Roadmap

\`\`\`yaml flow
kind: roadmap
milestones:
  - m0
\`\`\`
`,
  );
  write(
    root,
    ".agents/flow/milestones/m0-foundation.md",
    `# Foundation

\`\`\`yaml flow
kind: milestone
id: m0
title: Foundation
status: active
github:
  number: null
  url: null
\`\`\`
`,
  );
  return root;
};

test("creates a bilingual issue draft that belongs to the roadmap", async (context) => {
  const root = createTree(context);
  const paths = await createIssue({
    root,
    milestone: "m0",
    priority: "P0",
    slug: "first-gate",
    title: "首个门禁",
    titleEn: "First gate",
    scopes: ["scripts/**", ".agents/flow/issues/m0-first-gate.*"],
  });
  assert.equal(existsSync(paths.base), true);
  assert.equal(existsSync(paths.en), true);
  assert.equal(existsSync(paths.meta), true);
  assert.match(readFileSync(paths.base, "utf8"), /status: backlog/);
  assert.match(readFileSync(paths.meta, "utf8"), /m0-first-gate\.md: [0-9a-f]{40}/);
});

test("creates milestones before issues and backfills their GitHub references", async () => {
  const tree = {
    roadmap: { file: ".agents/flow/roadmap.md", data: { milestones: ["m0"] } },
    milestones: [
      {
        file: ".agents/flow/milestones/m0-foundation.md",
        data: { id: "m0", title: "Foundation", status: "active", github: { number: null, url: null } },
      },
    ],
    issues: [
      {
        file: ".agents/flow/issues/m0-first.md",
        name: "m0-first",
        data: {
          milestone: "m0",
          status: "backlog",
          github: { number: null, url: null },
        },
      },
    ],
  };
  const calls = [];
  const result = await runSync(tree, {
    async createMilestone(milestone) {
      calls.push(`milestone:${milestone.data.id}`);
      return { number: 3, url: "https://github.com/o/r/milestone/3" };
    },
    async createIssue(issue, milestone) {
      calls.push(`issue:${issue.name}:${milestone.data.id}`);
      return { number: 9, url: "https://github.com/o/r/issues/9" };
    },
    async backfill(file, number) {
      calls.push(`backfill:${file}:${number}`);
    },
    async readMilestoneState() {
      return "open";
    },
    async updateMilestoneState() {},
    async readIssueState() {
      return "OPEN";
    },
    async updateIssueState() {},
  });
  assert.deepEqual(result, { milestones: 1, issues: 1, stateUpdates: 0 });
  assert.deepEqual(calls, [
    "milestone:m0",
    "backfill:.agents/flow/milestones/m0-foundation.md:3",
    "issue:m0-first:m0",
    "backfill:.agents/flow/issues/m0-first.md:9",
  ]);
});

test("installs a commit hook that blocks a missing issue reference", (context) => {
  const gitDir = mkdtempSync(path.join(tmpdir(), "ai-eval-flow-hooks-"));
  context.after(() => rmSync(gitDir, { recursive: true, force: true }));
  installGitHooks({ gitDir });
  const commitMessage = path.join(gitDir, "message.txt");
  writeFileSync(commitMessage, "change without a gate reference\n");
  const result = spawnSync(path.join(gitDir, "hooks", "commit-msg"), [commitMessage], {
    encoding: "utf8",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /issue 引用/);
});

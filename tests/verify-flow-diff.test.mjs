import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { verifyFlowDiff } from "../scripts/verify-flow.mjs";

const write = (root, relative, contents) => {
  const file = path.join(root, relative);
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, contents);
};

const issue = (status) => `# First issue

\`\`\`yaml flow
kind: issue
milestone: m0
priority: P0
status: ${status}
scope:
  - src/**
  - .agents/flow/issues/m0-01-first.md
adr: []
github:
  number: 7
  url: https://github.com/o/r/issues/7
\`\`\`
`;

const baseFiles = (status) => ({
  ".agents/flow/roadmap.md": `# Roadmap

\`\`\`yaml flow
kind: roadmap
milestones:
  - m0
\`\`\`
`,
  ".agents/flow/milestones/m0-first.md": `# M0

\`\`\`yaml flow
kind: milestone
id: m0
title: M0
status: active
github:
  number: null
  url: null
\`\`\`
`,
  ".agents/flow/issues/m0-01-first.md": issue(status),
});

test("allows a PR that activates a backlog issue in the same branch", async (context) => {
  const root = mkdtempSync(path.join(tmpdir(), "ai-eval-flow-diff-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  for (const [relative, contents] of Object.entries(baseFiles("backlog")))
    write(root, relative, contents);

  const git = (args, options = {}) =>
    execFileSync(
      "git",
      ["-c", "user.name=test", "-c", "user.email=test@example.com", ...args],
      { cwd: root, encoding: "utf8", ...options },
    );
  git(["init", "-q"]);
  git(["add", "."]);
  git(["commit", "-qm", "initial"]);

  write(root, ".agents/flow/issues/m0-01-first.md", issue("ready"));
  write(root, "src/index.js", "export {};\n");
  git(["add", "."]);
  git(["commit", "-qm", "activate (#7)"]);

  const previous = process.cwd();
  const previousTitle = process.env.PR_TITLE;
  process.chdir(root);
  process.env.PR_TITLE = "activate gate (#7)";
  try {
    const result = await verifyFlowDiff("HEAD~1");
    assert.equal(result.ok, true, result.errors.join("\n"));
  } finally {
    process.chdir(previous);
    if (previousTitle === undefined) delete process.env.PR_TITLE;
    else process.env.PR_TITLE = previousTitle;
  }
});

test("rejects a PR change outside the referenced issue scope", async (context) => {
  const root = mkdtempSync(path.join(tmpdir(), "ai-eval-flow-scope-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  for (const [relative, contents] of Object.entries(baseFiles("ready")))
    write(root, relative, contents);

  const git = (args) =>
    execFileSync(
      "git",
      ["-c", "user.name=test", "-c", "user.email=test@example.com", ...args],
      { cwd: root, encoding: "utf8" },
    );
  git(["init", "-q"]);
  git(["add", "."]);
  git(["commit", "-qm", "initial (#7)"]);
  write(root, "docs/out-of-scope.md", "# Out of scope\n");
  git(["add", "."]);
  git(["commit", "-qm", "change docs (#7)"]);

  const previous = process.cwd();
  const previousTitle = process.env.PR_TITLE;
  process.chdir(root);
  process.env.PR_TITLE = "change docs (#7)";
  try {
    const result = await verifyFlowDiff("HEAD~1");
    assert.equal(result.ok, false);
    assert.match(result.errors.join("\n"), /docs\/out-of-scope\.md/);
  } finally {
    process.chdir(previous);
    if (previousTitle === undefined) delete process.env.PR_TITLE;
    else process.env.PR_TITLE = previousTitle;
  }
});

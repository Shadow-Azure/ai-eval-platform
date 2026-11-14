import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import {
  collectRefs,
  loadFlowTree,
  parseFlowDocument,
  uncoveredFiles,
  validateTree,
} from "../scripts/flow-lib.mjs";

const roadmap = (ids = ["m0", "m1"]) => `# Roadmap

\`\`\`yaml flow
kind: roadmap
milestones:
${ids.map((id) => `  - ${id}`).join("\n")}
\`\`\`
`;

const milestone = (id, status = "active", keepOpen = null) => `# ${id}

\`\`\`yaml flow
kind: milestone
id: ${id}
title: ${id} title
status: ${status}
${keepOpen === null ? "" : `keep-open: ${keepOpen}\n`}github:
  number: null
  url: null
\`\`\`
`;

const issue = ({ name = "m0-01-first", status = "backlog", number = null, url = null, bootstrap = null } = {}) => `# Issue

\`\`\`yaml flow
kind: issue
milestone: m0
priority: P0
status: ${status}
${bootstrap === null ? "" : `bootstrap: ${bootstrap}\n`}scope:
  - src/**
adr: []
github:
  number: ${number}
  url: ${url}
\`\`\`

File: ${name}
`;

const fixture = (files) => {
  const root = mkdtempSync(path.join(tmpdir(), "ai-eval-flow-"));
  for (const [relative, contents] of Object.entries(files)) {
    const file = path.join(root, relative);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, contents);
  }
  return root;
};

test("parses a single flow fence", () => {
  const document = parseFlowDocument(issue({ status: "ready", number: 12, url: '"https://github.com/o/r/issues/12"' }), "issue.md");
  assert.equal(document.kind, "issue");
  assert.deepEqual(document.data.scope, ["src/**"]);
  assert.deepEqual(document.data.github, {
    number: 12,
    url: "https://github.com/o/r/issues/12",
  });
});

test("rejects a flow document without exactly one fence", () => {
  assert.throws(() => parseFlowDocument("# No fence\n", "issue.md"), /恰好一个/);
});

test("accepts a structurally valid roadmap, milestone, and backlog issue", async () => {
  const root = fixture({
    ".agents/flow/roadmap.md": roadmap(),
    ".agents/flow/milestones/m0-first.md": milestone("m0"),
    ".agents/flow/milestones/m1-second.md": milestone("m1", "planned"),
    ".agents/flow/issues/m0-01-first.md": issue(),
  });
  try {
    const { tree, errors } = await loadFlowTree(path.join(root, ".agents/flow"));
    assert.deepEqual(errors, []);
    assert.deepEqual(validateTree(tree), []);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects an unsynced issue that has advanced beyond backlog", async () => {
  const root = fixture({
    ".agents/flow/roadmap.md": roadmap(),
    ".agents/flow/milestones/m0-first.md": milestone("m0"),
    ".agents/flow/milestones/m1-second.md": milestone("m1", "planned"),
    ".agents/flow/issues/m0-01-first.md": issue({ status: "ready" }),
  });
  try {
    const { tree } = await loadFlowTree(path.join(root, ".agents/flow"));
    assert.match(validateTree(tree).join("\n"), /draft 只能处于 backlog/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("extracts only explicit issue references", () => {
  assert.deepEqual(collectRefs("implement (#12) and [#3]"), [12, 3]);
  assert.deepEqual(collectRefs("bare #9 does not count"), []);
});

test("reports changed files outside the issue scope union", () => {
  assert.deepEqual(uncoveredFiles(["src/a.js", "docs/a.md"], ["src/**"]), ["docs/a.md"]);
});

test("accepts a keep-open milestone when every issue is done", async () => {
  const root = fixture({
    ".agents/flow/roadmap.md": roadmap(["m0"]),
    ".agents/flow/milestones/m0-first.md": milestone("m0", "active", true),
    ".agents/flow/issues/m0-01-first.md": issue({
      status: "done",
      number: 1,
      url: "https://github.com/o/r/issues/1",
    }),
  });
  try {
    const { tree, errors } = await loadFlowTree(path.join(root, ".agents/flow"));
    assert.deepEqual(errors, []);
    assert.deepEqual(validateTree(tree), []);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("still requires milestone done once every issue is done without keep-open", async () => {
  const root = fixture({
    ".agents/flow/roadmap.md": roadmap(["m0"]),
    ".agents/flow/milestones/m0-first.md": milestone("m0", "active"),
    ".agents/flow/issues/m0-01-first.md": issue({
      status: "done",
      number: 1,
      url: "https://github.com/o/r/issues/1",
    }),
  });
  try {
    const { tree } = await loadFlowTree(path.join(root, ".agents/flow"));
    assert.match(validateTree(tree).join("\n"), /全部 issue 已 done/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects a non-boolean keep-open", async () => {
  const root = fixture({
    ".agents/flow/roadmap.md": roadmap(["m0"]),
    ".agents/flow/milestones/m0-first.md": milestone("m0", "active", "yes"),
    ".agents/flow/issues/m0-01-first.md": issue(),
  });
  try {
    const { tree } = await loadFlowTree(path.join(root, ".agents/flow"));
    assert.match(validateTree(tree).join("\n"), /keep-open 只能是 true\/false/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

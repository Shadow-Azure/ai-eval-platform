import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, describe, it } from "node:test";

import {
  blobHash,
  renderPairRecord,
} from "../scripts/translation-pairing-lib.mjs";

const tempDirs = [];

function createGateRunner(gateModule, prefix) {
  let sequence = 0;

  async function run(files) {
    const dir = mkdtempSync(path.join(tmpdir(), `ai-eval-${prefix}-`));
    tempDirs.push(dir);
    for (const [relative, content] of Object.entries(files)) {
      const full = path.join(dir, relative);
      mkdirSync(path.dirname(full), { recursive: true });
      writeFileSync(full, content);
    }

    const previous = process.cwd();
    process.chdir(dir);
    try {
      const specifier = new URL(
        `../scripts/${gateModule}?fixture=${sequence++}`,
        import.meta.url,
      );
      const gate = await import(specifier.href);
      return await gate.default();
    } finally {
      process.chdir(previous);
    }
  }

  return { run };
}

after(() => {
  for (const dir of tempDirs) rmSync(dir, { recursive: true, force: true });
});

describe("documentation index", () => {
  const gate = createGateRunner("verify-doc-index.mjs", "doc-index");

  it("accepts registered documents and rejects missing or ghost entries", async () => {
    const valid = await gate.run({
      "docs/README.md":
        "# Docs\n\n| Document | Purpose |\n|---|---|\n| [a](a.md) | A |\n",
      "docs/a.md": "# A\n",
      "docs/a.en.md": "# A\n",
    });
    assert.equal(valid.ok, true);

    const invalid = await gate.run({
      "docs/README.md":
        "# Docs\n\n| Document | Purpose |\n|---|---|\n| [a](a.md) | A |\n| [ghost](ghost.md) | G |\n",
      "docs/a.md": "# A\n",
      "docs/c.md": "# C\n",
    });
    assert.equal(invalid.ok, false);
    assert.deepEqual(invalid.errors, [
      "docs/c.md: 未登记进 docs/README.md 地图表",
      "docs/README.md: 登记的 docs/ghost.md 不存在",
    ]);
  });
});

describe("Markdown wrapping", () => {
  const gate = createGateRunner("verify-md-wrap.mjs", "doc-wrap");

  it("rejects prose that spans physical lines", async () => {
    const result = await gate.run({
      "docs/architecture.md": "# Architecture\n\nA paragraph is split\nacross two lines.\n",
    });
    assert.equal(result.ok, false);
    assert.match(result.errors[0], /architecture\.md:3/);
  });

  it("allows tables, code fences, and list items on one line", async () => {
    const result = await gate.run({
      "docs/architecture.md": [
        "# Architecture",
        "",
        "| A | B |",
        "|---|---|",
        "| 1 | 2 |",
        "",
        "- one line",
        "- another line",
        "",
        "```js",
        "const split =",
        "  true;",
        "```",
      ].join("\n"),
    });
    assert.equal(result.ok, true);
  });
});

describe("Markdown links", () => {
  const gate = createGateRunner("verify-md-links.mjs", "doc-links");

  it("rejects dead files and dead anchors", async () => {
    const result = await gate.run({
      "docs/README.md": "# Docs\n",
      "docs/a.md": "# A\n\n[dead](missing.md) and [anchor](b.md#missing).\n",
      "docs/b.md": "# B\n\n## Present\n",
    });
    assert.equal(result.ok, false);
    assert.deepEqual(result.errors, [
      "docs/a.md: 死链 → missing.md",
      "docs/a.md: 死锚 → b.md#missing",
    ]);
  });

  it("accepts real headings and explicit anchors", async () => {
    const result = await gate.run({
      "docs/README.md": "# Docs\n",
      "docs/a.md": "# A\n\n[heading](b.md#present) and [anchor](b.md#explicit).\n",
      "docs/b.md": '# B\n\n## Present\n\n<a id="explicit"></a>\n',
    });
    assert.equal(result.ok, true);
  });
});

describe("documentation budgets", () => {
  const gate = createGateRunner("verify-doc-budgets.mjs", "doc-budget");
  const manifest = (entries) =>
    JSON.stringify({ "//": "resident document limits", ...entries });

  it("requires every resident document to be registered", async () => {
    const result = await gate.run({
      "scripts/doc-budgets.manifest.json": manifest({}),
      "docs/new.md": "# New\n",
    });
    assert.equal(result.ok, false);
    assert.match(result.errors.join("\n"), /docs\/new\.md: 常驻文档未登记预算/);
  });

  it("rejects a document over its ceiling", async () => {
    const result = await gate.run({
      "scripts/doc-budgets.manifest.json": manifest({ "docs/long.md": 2 }),
      "docs/long.md": "# Long\n\nThis document is too long.\n",
    });
    assert.equal(result.ok, false);
    assert.match(result.errors.join("\n"), /超预算 2/);
  });
});

describe("bilingual pairing", () => {
  const gate = createGateRunner("verify-translation-pairing.mjs", "doc-pair");
  const manifest = JSON.stringify({ excluded: [] });
  const paths = {
    base: "docs/guide.md",
    en: "docs/guide.en.md",
    meta: "docs/guide.i18n.yaml",
  };

  function trio({ stale = false } = {}) {
    const base = "# 指南\n\n[English](guide.en.md) | 中文\n\n正文。\n";
    const en = "# Guide\n\nEnglish | [中文](guide.md)\n\nBody.\n";
    const meta = renderPairRecord(paths, {
      baseHash: blobHash(stale ? `${base}\nstale` : base),
      enHash: blobHash(en),
    });
    return {
      [paths.base]: base,
      [paths.en]: en,
      [paths.meta]: meta,
      "scripts/translation-pairing.manifest.json": manifest,
    };
  }

  it("accepts a complete, recorded trio", async () => {
    const result = await gate.run(trio());
    assert.equal(result.ok, true);
  });

  it("rejects a side changed without re-recording", async () => {
    const result = await gate.run(trio({ stale: true }));
    assert.equal(result.ok, false);
    assert.match(result.errors.join("\n"), /最后确认状态不符/);
  });

  it("rejects a document without its English side", async () => {
    const result = await gate.run({
      [paths.base]: "# 指南\n",
      "scripts/translation-pairing.manifest.json": manifest,
    });
    assert.equal(result.ok, false);
    assert.match(result.errors.join("\n"), /必须双语成对/);
  });
});

describe("translation terminology", () => {
  const gate = createGateRunner("verify-terminology.mjs", "doc-terms");

  const terminology =
    "# 术语表\n\n[English](terminology.en.md) | 中文\n\n| 中文 | English | 禁用替写 |\n|---|---|---|\n| 门禁 | gate | checkpoint |\n";
  const terminologyEn =
    "# Terminology\n\nEnglish | [中文](terminology.md)\n\n| Chinese | English | Banned alternates |\n|---|---|---|\n| 门禁 | gate | checkpoint |\n";
  const prompt =
    "# 翻译提示词\n\n[English](translation-prompt.en.md) | 中文\n\n```text\n{{terminology}}\n{{source_document}}\n{{source_basename}}\n```\n";

  it("rejects banned terminology in English prose", async () => {
    const result = await gate.run({
      "docs/i18n/terminology.md": terminology,
      "docs/i18n/terminology.en.md": terminologyEn,
      "docs/i18n/translation-prompt.md": prompt,
      "docs/architecture.en.md": "# Architecture\n\nRun the checkpoint.\n",
    });
    assert.equal(result.ok, false);
    assert.match(result.errors.join("\n"), /architecture\.en\.md:3/);
    assert.match(result.errors.join("\n"), /gate/);
  });

  it("accepts the canonical term", async () => {
    const result = await gate.run({
      "docs/i18n/terminology.md": terminology,
      "docs/i18n/terminology.en.md": terminologyEn,
      "docs/i18n/translation-prompt.md": prompt,
      "docs/architecture.en.md": "# Architecture\n\nRun the gate.\n",
    });
    assert.equal(result.ok, true);
  });
});

describe("documentation gate wiring", () => {
  it("routes documentation and gate changes to the expected commands", async () => {
    const route = await import("../scripts/route-gates.mjs");
    assert.deepEqual(route.recommend(["docs/README.md"]).commands, ["verify:docs"]);
    assert.deepEqual(route.recommend(["scripts/verify-md-links.mjs"]).commands, [
      "test",
      "verify:docs",
    ]);
  });

  it("wires documentation checks into local scripts and CI", () => {
    const packageJson = JSON.parse(readFileSync("package.json", "utf8"));
    const workflow = readFileSync(".github/workflows/docs-gates.yml", "utf8");
    assert.equal(packageJson.scripts["lint:docs"], "node scripts/run-gates.mjs --mode doc-quick");
    assert.equal(packageJson.scripts["verify:docs"], "node scripts/run-gates.mjs --mode doc-sync");
    assert.match(workflow, /npm test/);
    assert.match(workflow, /npm run verify:docs/);
  });
});

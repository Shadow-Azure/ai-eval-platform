#!/usr/bin/env node
// Documentation gate orchestrator. Each gate is a leaf; modes are explicit
// combinations. CI runs doc-sync so a green pipeline cannot silently omit docs.
// Usage: node scripts/run-gates.mjs --mode doc-quick|doc-sync

import { execFileSync } from "node:child_process";
import { readdirSync } from "node:fs";

import verifyDocBudgets from "./verify-doc-budgets.mjs";
import verifyDocIndex from "./verify-doc-index.mjs";
import verifyDocRefs from "./verify-doc-refs.mjs";
import verifyMdLinks from "./verify-md-links.mjs";
import verifyMdWrap from "./verify-md-wrap.mjs";
import verifyTerminology from "./verify-terminology.mjs";
import verifyTranslationPairing from "./verify-translation-pairing.mjs";

function runNodeTests() {
  try {
    const tests = readdirSync("tests")
      .filter((name) => name.endsWith(".test.mjs"))
      .sort()
      .map((name) => `tests/${name}`);
    execFileSync(process.execPath, ["--test", ...tests], { stdio: "inherit" });
    return { ok: true, errors: [] };
  } catch {
    return {
      ok: false,
      errors: ["node --test 退出非零（门禁自测试失败，明细见上方输出）"],
    };
  }
}

export const LEAVES = {
  "verify-doc-index": verifyDocIndex,
  "verify-doc-budgets": verifyDocBudgets,
  "verify-md-wrap": verifyMdWrap,
  "verify-md-links": verifyMdLinks,
  "verify-doc-refs": verifyDocRefs,
  "verify-translation-pairing": () => verifyTranslationPairing([]),
  "verify-terminology": verifyTerminology,
  "gate-self-tests": runNodeTests,
};

export const MODES = {
  "doc-quick": [
    "verify-doc-index",
    "verify-doc-budgets",
    "verify-md-wrap",
    "verify-doc-refs",
    "verify-translation-pairing",
    "verify-terminology",
  ],
  "doc-sync": [
    "verify-doc-index",
    "verify-doc-budgets",
    "verify-md-wrap",
    "verify-md-links",
    "verify-doc-refs",
    "verify-translation-pairing",
    "verify-terminology",
    "gate-self-tests",
  ],
};

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  const modeIndex = args.indexOf("--mode");
  const mode = modeIndex >= 0 ? args[modeIndex + 1] : "doc-quick";
  if (!MODES[mode]) {
    console.error(`未知模式 "${mode}"，可选：${Object.keys(MODES).join(" | ")}`);
    process.exit(2);
  }

  const failures = [];
  for (const leaf of MODES[mode]) {
    const { ok, errors } = await LEAVES[leaf]();
    if (ok) {
      console.log(`✓ ${leaf}`);
    } else {
      failures.push(leaf);
      console.error(`✗ ${leaf}\n    ${errors.join("\n    ")}`);
    }
  }

  if (failures.length) {
    console.error(`\n${failures.length} 个门禁失败：${failures.join(", ")}`);
    process.exit(1);
  }
  console.log(`\n${mode}: 全绿（${MODES[mode].length} 叶）`);
}

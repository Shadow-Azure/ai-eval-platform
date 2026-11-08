#!/usr/bin/env node
// Gate: 双语三件套配对（契约 home：docs/i18n/README.md）。
// 检查：① 语料内 base 必须有英文侧；② 存在即完整（三件套齐）且一致
// （两侧 blob hash 与 .i18n.yaml 记录相符、两侧语言切换行、链接 locale、
// 生成区字节一致、结构签名镜像）；③ 豁免件不得携带 .en.md/.i18n.yaml。
// 模式：无参=全库检查；`docs/foo.md` 等具名=只查该对；--list=状态报告；
// --write <pair|--all>=重录确认记录。改任一侧 → 最小修补另一侧 → --write 重录。

import { readdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import {
  isScopeFile,
  pairPaths,
  blobHash,
  FENCE_OPEN_RE,
  parseSignature,
  structureDiff,
  partitionGeneratedRegions,
  generatedRegionsEqual,
  parsePairRecord,
  renderPairRecord,
  parsePairingManifest,
  isManifestExcluded,
  parsePairingCliArgs,
  isSwitcherLine,
  markdownLinks,
} from "./translation-pairing-lib.mjs";

const MANIFEST = "scripts/translation-pairing.manifest.json";

/** 收集一个目录树下的语料工件（.md/.en.md/.i18n.yaml）。 */
async function collectTree(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "archived" && dir.endsWith(path.join(".agents", "notes"))) continue;
      await collectTree(full, out);
    } else if (/\.(md|en\.md|i18n\.yaml)$/.test(entry.name) || entry.name.endsWith(".i18n.yaml")) {
      const rel = path.relative("", full).split(path.sep).join("/");
      if (isScopeFile(rel)) out.push(rel);
    }
  }
  return out;
}

/** 发现全库语料：docs/、.agents/notes/（除 archived）、.agents/flow/、根 README 三件套。 */
async function discoverCorpus() {
  const files = new Set(["README.md", "README.en.md", "README.i18n.yaml"]);
  for (const root of ["docs", ".agents/notes", ".agents/flow"])
    for (const file of await collectTree(root)) files.add(file);
  return [...files].filter((f) => existsSync(f)).sort();
}

/** 期望的切换行（base 侧链去英文侧，en 侧链回 base）；位置：docs 紧随 H1，
 *  Agent Note 在 Status 行之后（前三行格式是另一门禁的固定契约）。 */
function expectedSwitcher(side, counterpartBasename) {
  return side === "base"
    ? `[English](${counterpartBasename}) | 中文`
    : `English | [中文](${counterpartBasename})`;
}

/**
 * 链接 locale 校验：指向语料内文档的相对链接，base 侧用 `.md`、en 侧用 `.en.md`。
 * 行内代码与围栏里的"链接"是示例（如 i18n README 引用切换行原文），不算数；
 * 行号按原文保留（逐行清洗，不删除行）。
 * @returns {string[]} 违规描述。
 */
function localeViolations(text, relPath, side, manifest) {
  const violations = [];
  const lines = text.split("\n");
  let inFence = false;
  const prose = lines
    .map((line) => {
      if (FENCE_OPEN_RE.test(line)) {
        inFence = !inFence;
        return "";
      }
      if (inFence) return "";
      return line.replace(/`[^`\n]*`/g, " ");
    })
    .join("\n");
  const seen = new Set();
  for (const { line, target } of markdownLinks(prose)) {
    if (isSwitcherLine(lines[line - 1] ?? "")) continue; // 切换行本身跨语言，豁免
    if (/^[a-z]+:\/\//i.test(target) || target.startsWith("#")) continue;
    const pathPart = target.split(/[?#]/)[0];
    if (!pathPart.endsWith(".md") && !pathPart.endsWith(".en.md")) continue;
    // 仓库相对解析（path.join 归一 ../）：isScopeFile 吃仓库相对路径，
    // path.resolve 会产出绝对路径、令语料判定恒假。
    const resolved = path
      .join(path.dirname(relPath), decodeURIComponent(pathPart))
      .split(path.sep)
      .join("/");
    const collapsed = resolved.replace(/\.en\.md$/, ".md");
    if (!isScopeFile(collapsed) || isManifestExcluded(collapsed, manifest)) continue;
    const key = `${relPath}:${line}:${target}`;
    if (seen.has(key)) continue;
    if (side === "en" && !pathPart.endsWith(".en.md"))
      violations.push(`${relPath}:${line} 链接 ${target} 应指向英文侧 ${pathPart.replace(/\.md$/, ".en.md")}`);
    if (side === "base" && pathPart.endsWith(".en.md"))
      violations.push(`${relPath}:${line} 中文侧链接 ${target} 应指向中文侧 ${pathPart.replace(/\.en\.md$/, ".md")}`);
    seen.add(key);
  }
  return violations;
}

/** 对一对完整三件套做全部一致性检查；返回错误列表（空=一致）。 */
function checkPair(paths, contents, manifest) {
  const errors = [];
  const baseName = paths.base.split("/").pop();
  const enName = paths.en.split("/").pop();

  const record = parsePairRecord(contents.meta, paths);
  if (!record) {
    errors.push(`${paths.meta}: 记录格式非法（期望恰好 \`${baseName}: <40-hex>\` 与 \`${enName}: <40-hex>\` 两行）`);
    return errors;
  }
  if (blobHash(contents.base) !== record.baseHash)
    errors.push(`${paths.base}: 内容与 ${paths.meta} 记录的最后确认状态不符（带上另一侧后 --write 重录）`);
  if (blobHash(contents.en) !== record.enHash)
    errors.push(`${paths.en}: 内容与 ${paths.meta} 记录的最后确认状态不符（带上另一侧后 --write 重录）`);
  if (errors.length) return errors;

  // 切换行：存在且形状精确（指向正确的对侧文件）。
  for (const [side, rel, counterpart] of [
    ["base", paths.base, enName],
    ["en", paths.en, baseName],
  ]) {
    const lines = contents[side].split("\n");
    const expected = expectedSwitcher(side, counterpart);
    if (!lines.includes(expected))
      errors.push(`${rel}: 缺语言切换行（应有整行 "${expected}"）`);
  }

  errors.push(...localeViolations(contents.base, paths.base, "base", manifest));
  errors.push(...localeViolations(contents.en, paths.en, "en", manifest));

  // 生成区：除 locale 投影的配对文档路径外逐字节一致。
  for (const [rel, text] of [
    [paths.base, contents.base],
    [paths.en, contents.en],
  ]) {
    try {
      partitionGeneratedRegions(text);
    } catch (error) {
      errors.push(`${rel}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  if (errors.length) return errors;
  if (!generatedRegionsEqual(
    partitionGeneratedRegions(contents.base).regions,
    partitionGeneratedRegions(contents.en).regions,
  ))
    errors.push(`${paths.base} ↔ ${paths.en}: 生成区不一致（重新跑 gen:commands 写两侧）`);

  // 结构签名镜像。
  let baseSig;
  let enSig;
  try {
    baseSig = parseSignature(contents.base);
    enSig = parseSignature(contents.en);
  } catch (error) {
    errors.push(`${paths.base}: ${error instanceof Error ? error.message : String(error)}`);
    return errors;
  }
  errors.push(...structureDiff(baseSig, enSig));
  return errors;
}

/** 无参/具名检查主流程；返回 {ok, errors, state}。 */
export default async function verifyTranslationPairing(argv = []) {
  const request = parsePairingCliArgs(argv);
  const manifest = parsePairingManifest(await readFile(MANIFEST, "utf8"));
  const root = path.resolve("");

  // 语料发现：全库，或具名 pair 的三件套。
  const fileSet = new Set();
  if (request.scope === "pairs") {
    for (const anchor of request.anchors) {
      const { base, en, meta } = pairPaths(anchor);
      for (const file of [base, en, meta]) if (existsSync(path.join(root, file))) fileSet.add(file);
      if (!existsSync(path.join(root, base))) fileSet.add(base); // 报缺件
      const rejected = !isScopeFile(anchor) || isManifestExcluded(anchor, manifest);
      if (rejected) return { ok: false, errors: [`${anchor}: 不在配对语料内（豁免件或语料外，见 docs/i18n/README.md）`] };
    }
  } else {
    for (const file of await discoverCorpus()) fileSet.add(file);
  }

  const files = [...fileSet].sort();
  const bases = files.filter((f) => f.endsWith(".md") && !f.endsWith(".en.md"));
  const enSides = files.filter((f) => f.endsWith(".en.md"));
  const metas = files.filter((f) => f.endsWith(".i18n.yaml"));

  const errors = [];
  const state = new Map();
  const read = async (file) =>
    existsSync(path.join(root, file)) ? await readFile(path.join(root, file)) : undefined;

  // ① 语料内 base 必须有英文侧。
  for (const base of bases) {
    if (isManifestExcluded(base, manifest)) continue;
    if (!existsSync(path.join(root, base))) continue; // 幽灵锚点交给 ② 报缺件
    if (!existsSync(path.join(root, base.replace(/\.md$/, ".en.md")))) {
      errors.push(`${base}: 语料内文档必须双语成对（docs/i18n/README.md），补英文侧并 --write 记录`);
      state.set(base, "missing");
    }
  }

  // ② 存在即完整且一致：以 en 侧与记录的并集为锚，半删的三件套从任一残留都能抓到。
  const anchors = new Set([
    ...bases,
    ...enSides.map((f) => f.replace(/\.en\.md$/, ".md")),
    ...metas.map((f) => f.replace(/\.i18n\.yaml$/, ".md")),
  ]);

  for (const base of [...anchors].sort()) {
    const paths = pairPaths(base);
    const have = {
      base: existsSync(path.join(root, paths.base)),
      en: existsSync(path.join(root, paths.en)),
      meta: existsSync(path.join(root, paths.meta)),
    };
    const excluded = isManifestExcluded(base, manifest);

    if (excluded) {
      if (have.en) errors.push(`${paths.en}: ${base} 已豁免配对，英文侧必须不存在`);
      if (have.meta) errors.push(`${paths.meta}: ${base} 已豁免配对，记录必须不存在`);
      continue;
    }
    if (!have.base || !have.en || !have.meta) {
      const missing = [
        ...(have.base ? [] : [paths.base]),
        ...(have.en ? [] : [paths.en]),
        ...(have.meta ? [] : [paths.meta]),
      ];
      errors.push(`${base}: 三件套不完整——缺 ${missing.join("、")}（成对合并：两侧语言 + 记录同 PR 落地）`);
      state.set(base, "missing");
      continue;
    }

    const contents = {
      base: await read(paths.base),
      en: await read(paths.en),
      meta: await read(paths.meta),
    };
    const pairErrors = checkPair(paths, {
      base: contents.base.toString("utf8"),
      en: contents.en.toString("utf8"),
      meta: contents.meta.toString("utf8"),
    }, manifest);
    errors.push(...pairErrors);
    if (!state.has(base)) state.set(base, pairErrors.length ? "out-of-sync" : "ok");
  }

  if (request.mode === "list") {
    const order = { "out-of-sync": 0, missing: 1, ok: 2 };
    const rows = [...state.entries()].sort((a, b) => order[a[1]] - order[b[1]] || a[0].localeCompare(b[0]));
    for (const [file, status] of rows)
      console.log(`${status.padEnd(12)} ${file}${status === "missing" ? "  （必须配对）" : ""}`);
    const counts = { ok: 0, "out-of-sync": 0, missing: 0 };
    for (const status of state.values()) counts[status] += 1;
    console.log(`verify-translation-pairing: ${counts.ok} ok / ${counts["out-of-sync"]} 漂移 / ${counts.missing} 缺对（共 ${state.size} 对在册）`);
    return { ok: true, errors: [] };
  }

  return { ok: errors.length === 0, errors };
}

/** --write：为确认过的 pair 重录两侧 hash（生成缺失的记录）。 */
async function writeRecords(request) {
  const root = path.resolve("");
  const manifest = parsePairingManifest(await readFile(MANIFEST, "utf8"));
  const corpus = await discoverCorpus();
  const targets =
    request.scope === "pairs"
      ? request.anchors
      : corpus.filter((f) => f.endsWith(".md") && !f.endsWith(".en.md") && !isManifestExcluded(f, manifest));

  let written = 0;
  for (const base of targets) {
    if (isManifestExcluded(base, manifest))
      throw new Error(`${base}: 豁免件没有配对记录可写`);
    const paths = pairPaths(base);
    if (!existsSync(path.join(root, paths.base)) || !existsSync(path.join(root, paths.en))) {
      // 具名 pair 缺件即失败；全量模式跳过缺对（检查门禁会报）。
      if (request.scope === "pairs")
        throw new Error(`${base}: 三件套不完整（缺 ${existsSync(path.join(root, paths.base)) ? paths.en : paths.base}），无法记录`);
      continue;
    }
    const record = renderPairRecord(paths, {
      baseHash: blobHash(await readFile(path.join(root, paths.base))),
      enHash: blobHash(await readFile(path.join(root, paths.en))),
    });
    const metaPath = path.join(root, paths.meta);
    if (existsSync(metaPath) && (await readFile(metaPath, "utf8")) === record) continue;
    await writeFile(metaPath, record);
    console.log(`verify-translation-pairing: 已记录 ${paths.meta}`);
    written += 1;
  }
  console.log(`verify-translation-pairing: 写入 ${written} 条记录；跑检查验证配对一致性。`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const request = parsePairingCliArgs(process.argv.slice(2));
    if (request.mode === "write") {
      await writeRecords(request);
      process.exit(0);
    }
    const result = await verifyTranslationPairing(process.argv.slice(2));
    if (!result.ok) {
      console.error(`verify-translation-pairing: FAIL\n  ${result.errors.join("\n  ")}`);
      process.exit(1);
    }
    console.log("verify-translation-pairing: ok");
  } catch (error) {
    console.error(`verify-translation-pairing: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(2);
  }
}

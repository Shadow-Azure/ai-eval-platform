#!/usr/bin/env node
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const templatePath = resolve(scriptDir, "../template/report.template.html");
const usage = "usage: render-smoke-report.mjs <report-data.json> <output.html>";

function fail(message) {
  console.error(`render-smoke-report: FAIL ${message}`);
  process.exit(1);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function arrayOf(value) {
  return Array.isArray(value) ? value : [];
}

function asText(value) {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function status(item) {
  const raw = item?.status ?? {};
  const kinds = new Set(["pass", "fixed", "fail", "warn", "manual", "info"]);
  const kind = kinds.has(raw.kind) ? raw.kind : "info";
  const label = asText(raw.label) || kind.toUpperCase();
  return { kind, label: escapeHtml(label) };
}

function relativeAsset(src, dataDir, outputDir, missing, context) {
  const raw = String(src ?? "");
  if (!raw) throw new Error(`${context} has an empty src`);
  if (/^(https?|data):/i.test(raw)) return raw;
  const source = isAbsolute(raw) ? raw : resolve(dataDir, raw);
  if (!existsSync(source)) missing.push(`${context}: ${source}`);
  return relative(outputDir, source).split(/[/\\]/).join("/");
}

function metricCard(item) {
  return `<div class="metric"><b>${escapeHtml(item?.value)}</b><span>${escapeHtml(item?.label)}</span></div>`;
}

function factPair(item) {
  return `<div class="fact"><dt>${escapeHtml(item?.label)}</dt><dd>${escapeHtml(item?.value)}</dd></div>`;
}

function evidenceText(value) {
  const items = arrayOf(value).map((item) => escapeHtml(item));
  return items.length ? items.join("<br>") : "—";
}

function renderMatrix(matrix) {
  const columns = arrayOf(matrix.columns);
  if (!columns.length) throw new Error("matrix.columns must contain at least one column");
  const rows = arrayOf(matrix.rows);
  const head = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
  const body = rows.map((row) => {
    const badge = status(row);
    const cells = columns.map((_, index) => {
      if (index === 1) return `<td>${escapeHtml(row.name)}</td>`;
      if (index === 2) return `<td><span class="status ${badge.kind}">${badge.label}</span></td>`;
      if (index === 3) return `<td>${escapeHtml(row.detail)}</td>`;
      if (index === 4) return `<td class="evidence">${evidenceText(row.evidence)}</td>`;
      if (index === 0) return `<td class="num">${escapeHtml(row.id ?? index + 1)}</td>`;
      return "<td></td>";
    });
    return `<tr>${cells.join("")}</tr>`;
  });
  const table = `<thead><tr>${head}</tr></thead><tbody>${body.join("")}</tbody>`;
  const counts = rows.reduce((counts, row) => {
    const kind = status(row).kind;
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
    return counts;
  }, new Map());
  const summary = [...counts.entries()].map(([kind, count]) => `${count} ${kind}`).join(" · ");
  return { table: `<table>${table}</table>`, summary: summary || "no scenarios", count: rows.length };
}

function renderIssues(issues) {
  if (!issues.length) return '<div class="empty">No defects were found.</div>';
  const cards = issues.map((issue) => {
    const badge = status(issue.state ?? issue);
    const facts = arrayOf(issue.facts).map((fact) => {
      return `<div><dt>${escapeHtml(fact?.label)}</dt><dd>${escapeHtml(fact?.value)}</dd></div>`;
    }).join("");
    const state = asText(issue.state?.label)
      ? `<div class="fixed">${escapeHtml(issue.state.label)}</div>`
      : "";
    return `<article class="issueCard"><span class="tag">${escapeHtml(issue.severity ?? "Observation")}</span><h3>${escapeHtml(issue.title)}</h3><dl>${facts}</dl>${state}</article>`;
  }).join("");
  return `<div class="issues">${cards}</div>`;
}

function renderScreenshots(screenshots, dataDir, outputDir, missing) {
  if (!screenshots.length) return '<div class="empty">No screenshots were attached.</div>';
  return screenshots.map((shot) => {
    const href = relativeAsset(shot.src, dataDir, outputDir, missing, `screenshot ${shot.label || shot.src}`);
    const variant = ["wide", "issue"].includes(shot.variant) ? shot.variant : "";
    return `<figure class="${variant}"><img loading="lazy" src="${escapeHtml(href)}" alt="${escapeHtml(shot.alt || shot.caption || "Smoke evidence")}"><figcaption><b>${escapeHtml(shot.label ?? "")}</b><span>${escapeHtml(shot.caption ?? "")}</span></figcaption></figure>`;
  }).join("");
}

function renderCoverage(coverage) {
  const columns = arrayOf(coverage.columns);
  const rows = arrayOf(coverage.rows);
  if (!columns.length || !rows.length) return '<div class="empty">No coverage assessment was attached.</div>';
  const head = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((_, index) => `<td>${escapeHtml(row[index])}</td>`).join("")}</tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderConclusion(conclusion) {
  const kinds = new Set(["pass", "warn", "fail", "info"]);
  const kind = kinds.has(conclusion.kind) ? conclusion.kind : "info";
  const points = arrayOf(conclusion.points).map((point) => `<li>${escapeHtml(point)}</li>`).join("");
  return { class: kind, html: `<p><strong>${escapeHtml(conclusion.summary)}</strong></p>${points ? `<ul>${points}</ul>` : ""}` };
}

const [dataArg, outputArg] = process.argv.slice(2);
if (!dataArg || !outputArg) fail(usage);

const dataPath = resolve(dataArg);
const outputPath = resolve(outputArg);
const dataDir = dirname(dataPath);
const outputDir = dirname(outputPath);

if (!existsSync(dataPath)) fail(`data file does not exist: ${dataPath}`);

let data;
try {
  data = JSON.parse(readFileSync(dataPath, "utf8"));
} catch (error) {
  fail(`data file is not valid JSON: ${error.message}`);
}

for (const field of ["title", "summary", "metrics", "facts", "matrix", "coverage", "conclusion"]) {
  if (data[field] === undefined) fail(`required field is missing: ${field}`);
}
if (!Array.isArray(data.metrics) || !data.metrics.length) fail("metrics must be a non-empty array");
if (!Array.isArray(data.facts) || !data.facts.length) fail("facts must be a non-empty array");

const missing = [];
const matrix = renderMatrix(data.matrix);
const issues = arrayOf(data.issues);
const screenshots = arrayOf(data.screenshots);
const issuesSummary = issues.length
  ? issues.map((issue) => escapeHtml(`${issue.severity ?? "Observation"} ${issue.title}`)).join("<br>")
  : "No defects found";
const screenshotSummary = screenshots.length ? `${screenshots.length} screenshots` : "no screenshots";
const coverageSummary = arrayOf(data.coverage.rows).length
  ? `${arrayOf(data.coverage.rows).length} areas reviewed`
  : "no coverage assessment";
const conclusion = renderConclusion(data.conclusion);

const template = readFileSync(templatePath, "utf8");
const replacements = {
  __LANG__: escapeHtml(asText(data.lang) || "en"),
  __TITLE__: escapeHtml(data.title),
  __EYEBROW__: escapeHtml(data.eyebrow ?? "Computer Use Smoke"),
  __SUMMARY__: escapeHtml(data.summary),
  __META__: escapeHtml(data.meta ?? ""),
  __METRICS__: data.metrics.map(metricCard).join(""),
  __FACTS__: data.facts.map(factPair).join(""),
  __MATRIX__: matrix.table,
  __MATRIX_SUMMARY__: matrix.summary,
  __ISSUES__: renderIssues(issues),
  __ISSUE_SUMMARY__: issuesSummary,
  __SCREENSHOTS__: renderScreenshots(screenshots, dataDir, outputDir, missing),
  __SCREENSHOT_SUMMARY__: screenshotSummary,
  __COVERAGE__: renderCoverage(data.coverage),
  __COVERAGE_SUMMARY__: coverageSummary,
  __CONCLUSION__: conclusion.html,
  __CONCLUSION_LABEL__: escapeHtml(data.conclusion.label ?? conclusion.kind),
  __CONCLUSION_KIND__: conclusion.class,
  __FOOTER__: data.footer ?? "",
};

let html = template;
for (const [token, value] of Object.entries(replacements)) {
  html = html.replaceAll(token, value);
}
if (/__[A-Z_]+__/.test(html)) fail("template still contains an unreplaced marker");

if (missing.length) fail(`missing screenshot evidence:\n${missing.map((item) => `  - ${item}`).join("\n")}`);

mkdirSync(outputDir, { recursive: true });
writeFileSync(outputPath, html);
console.log(`render-smoke-report: wrote ${outputPath}`);
console.log(`render-smoke-report: ${matrix.count} scenarios, ${issues.length} issues, ${screenshots.length} screenshots`);

---
name: computer-use-smoke-reporting
description: Use when Computer Use has exercised a packaged desktop or GUI application and screenshots, accessibility evidence, defects, and pass/fail results need to become a reusable HTML smoke report.
argument-hint: "[报告目录] [应用/版本/提交等上下文]"
disable-model-invocation: true
---

# Computer Use Smoke Reporting

## Overview

Collect Computer Use evidence first, model it as a JSON document, then render a standalone HTML report. Evidence is the source of truth; the HTML is generated and must not be hand-edited.

## Required Inputs

Collect these before rendering:

1. Report directory and output filename.
2. Application/version/commit/artifact identifiers.
3. Stable screenshot paths or files.
4. Scenario matrix with status and evidence.
5. Defects, severity, root cause, and fix state.
6. Automated-coverage assessment.

Use the local `computer-use` skill for desktop interaction and screenshots.

## Workflow

1. Create one report directory per run, for example:

   ```text
   reports/2026-10-10/example-app-v3/
   ```

2. Copy temporary screenshots into stable files. Decode `file://` URLs before copying. Prefer numbered names:

   ```text
   01-launch.png
   02-workspace-open.png
   03-defect-before-fix.png
   ```

3. Create `report-data.json` using `reference/data-schema.md`.

4. Render the report:

   ```sh
   node .agents/skills/computer-use-smoke-reporting/scripts/render-smoke-report.mjs \
     <report-dir>/report-data.json \
     <report-dir>/smoke-report.html
   ```

5. Validate:
   - every screenshot path exists;
   - no credentials, tokens, API keys, or private paths are exposed;
   - no template markers remain;
   - every scenario has evidence or an explicit reason evidence is unavailable;
   - every `FAIL → FIXED` claim has before/after evidence and regression coverage.

6. Open or share the generated HTML.

## Data Rules

- Put user-controlled strings in JSON; the renderer escapes HTML.
- Use asset paths relative to `report-data.json`.
- Use `wide` screenshots for full-window evidence and `issue` for defect close-ups.
- Use status kinds `pass`, `fixed`, `fail`, `warn`, `manual`, or `info`.
- Redact secrets as `<redacted>` before writing JSON.
- Do not put API keys in environment variables stored inside JSON.

## Reusability Rules

- Keep this skill app-agnostic.
- Put app-specific selectors, scenario names, release gates, and coverage decisions in `report-data.json`.
- Do not add product names, feature-specific selectors, or project paths to the template.
- If a new evidence type appears, extend the schema and renderer first, then use it.

## Common Mistakes

- Hand-editing generated HTML instead of updating JSON and rerunning.
- Writing `PASS` without a scenario assertion.
- Reporting a visual regression without before/after screenshots.
- Leaving provider keys, bearer tokens, or private absolute paths in screenshots or JSON.
- Claiming coverage from a real external service when the deterministic behavior was only mocked.

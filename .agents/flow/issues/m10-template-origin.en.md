# [m10] Show template provenance in evaluation records

English | [中文](m10-template-origin.md)

```yaml flow
kind: issue
milestone: m10
priority: P2
status: ready
scope:
  - prototype/**
  - web/src/pages/EvalsPage.tsx
  - web/src/api/evaluations.ts
  - web/src/components/TemplateBits.tsx
  - web/src/styles/overview.css
  - backend/app/services/evaluations.py
  - backend/app/services/eval_templates.py
  - backend/app/api/evaluations.py
  - tests/backend/test_evaluations.py
  - tests/backend/test_eval_templates.py
  - docs/design/eval-platform-architecture.md
  - docs/design/eval-platform-architecture.en.md
  - docs/design/eval-platform-architecture.i18n.yaml
  - scripts/doc-budgets.manifest.json
  - plans/**
  - .agents/flow/issues/m10-template-origin.md
  - .agents/flow/issues/m10-template-origin.en.md
  - .agents/flow/issues/m10-template-origin.i18n.yaml
adr: []
github:
  number: 410
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/410
```

## Background

In the production evaluation-task list (feedback on 2026-11-23), evaluations launched from an evaluation template and custom-launched evaluations have no distinguishable provenance: a row shows only the task name, run ID, and resource pool. The evaluation detail page already shows a “launched from evaluation template …” banner, but the list cannot tell which template a record came from or link from the record to the template. The user wants “from an evaluation template” to be directly visible in the evaluation records.

## Current state and gap

| Location | Current state | Gap |
|---|---|---|
| Production evaluation-task list | A row shows the task name, run ID, and resource pool | No source template, so template launches and custom launches are indistinguishable |
| Prototype evaluation-task list | A row shows the task name, run ID, and resource pool | Same as production; the source is missing too |
| Production evaluation detail | The detail API returns `template` and the header shows a source-template banner | The list has no source, so users still cannot tell the record was template-launched |
| Prototype evaluation detail | The banner appears only when the URL carries `?fromTemplate=` | Opening the record from the list hides the banner; provenance depends on the navigation parameter instead of the run record |
| Evaluation-list API | The summary returns `template_id` and `template_version` | It does not return the template name, so the list cannot render a readable origin |

## Goals

- Evaluation-task list: rows launched from a template show “来自模板 · template name”, and the template name opens the template detail; custom-launched rows stay unchanged.
- Evaluation detail: resolve the source template from the run record's `template_id` instead of depending on `?fromTemplate=`; template launches show the banner and custom evaluations do not.
- Do not add an execution API; the list summary already has `template_id` and `template_version`, so the production implementation may add the template name or reuse the template read API.
- Land the prototype first with before/after screenshots; the production implementation follows this issue.

## Acceptance

- A template-launched row shows the source label and template name; clicking the template name opens the corresponding template detail, while clicking elsewhere in the row still opens the evaluation detail.
- A custom-launched row shows no origin line.
- Opening a template-launched evaluation detail from the list shows the “launched from evaluation template …” banner; a custom evaluation does not.
- A long template name truncates and keeps a full title; table column widths and row heights keep the existing layout intact.
- Before/after screenshots use the same viewport, theme, and data, and are attached to this issue and the prototype PR.
- `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs`, and PR CI pass.

## Non-goals

- The prototype PR for this issue does not modify the production `web/` or `backend/`; the production implementation follows via the acceptance items.
- Do not add template filtering, grouped statistics, or a template-version column to the list.
- Do not change the evaluation execution, scheduling, scoring, or trace chain.

## References

- Related: issue #378 (evaluation templates), issue #394 (template launch history).
- Design: [docs/design/eval-platform-architecture.en.md](../../../docs/design/eval-platform-architecture.en.md) §5.14.

# [m10] Eval quick-start section position diverges from the prototype

English | [中文](m8-eval-quick-start-position.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - web/src/pages/EvalsPage.tsx
  - plans/**
  - .agents/flow/issues/m8-eval-quick-start-position.md
  - .agents/flow/issues/m8-eval-quick-start-position.en.md
  - .agents/flow/issues/m8-eval-quick-start-position.i18n.yaml
adr: []
github:
  number: 409
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/409
```

## Background

The production "Evaluations" page puts the quick-start section before the four metric cards. The prototype's `evalsPage()` order is page heading → four metric cards → quick start → evaluation list. After the #388 row-style fix, the user compared against the prototype and pointed out that the section position is still different. This is a page-structure gap #388 did not cover; the row styles themselves already match.

## Goals

- Move the quick-start section after the metric cards and before the evaluation list, in the same order as the prototype.
- Keep the quick-start section visible when the evaluation list fails to load, so the reorder does not hide it.
- Verify with before/after screenshots and a prototype screenshot at the same viewport and theme.

## Acceptance

- At 1500×837 the page order is: heading → four metric cards → quick start → evaluation list, the same as the prototype.
- The quick-start row keeps the #388 result: dark 13px / 700 name, tag plus time on one row, only details / launch, dashed separators.
- At 1200×837 the order is unchanged, the last launch is hidden, and there is no horizontal scrolling.
- `web` build, `npm test`, `npm run verify:flow` and `npm run verify:docs` pass.

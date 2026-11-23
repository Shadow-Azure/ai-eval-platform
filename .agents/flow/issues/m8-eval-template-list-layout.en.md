# [m10] Template list layout collapse under long names

English | [中文](m8-eval-template-list-layout.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - web/src/pages/EvalTemplatesPage.tsx
  - web/src/styles/templates.css
  - prototype/**
  - plans/**
  - .agents/flow/issues/m8-eval-template-list-layout.md
  - .agents/flow/issues/m8-eval-template-list-layout.en.md
  - .agents/flow/issues/m8-eval-template-list-layout.i18n.yaml
adr: []
github:
  number: 386
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/386
```

## Background

The production "eval templates" list collapses under a long template name: the template column grows to 658px, the fixed-combination column is squeezed to 71px, and its three lines break word by word, pushing the row height to 332px. The prototype keeps a 54px row height with the same data at 1500×837. The root cause is the antd `Table` default `table-layout: auto`, a template name rendered as a `Button type="link"` (antd defaults to `white-space: nowrap`), and no width or truncation constraints on the template/fixed-combination columns; the prototype's fixed table layout, fixed column widths and single-line ellipsis never reached the React implementation.

Production has one more column than the prototype: "cost estimate" (8 vs 7 columns). To keep the list's ability to surface unpriced templates early, this issue keeps that column and adds it to the prototype instead of removing it from production.

## Goals

- Switch the antd `Table` to a fixed layout, declare widths for all 8 columns, and allow horizontal scrolling on narrow screens so content cannot push column widths around.
- Truncate the template name to one line, the description to at most two lines, and each of the three fixed-combination lines to one line; expose full values through `title`.
- Top-align cell content so rows return to a compact height and action buttons stop breaking word by word.
- Add the "cost estimate" column to the prototype with the same 8-column width allocation as production.
- Verify with before/after screenshots at 1500×837 using a long-name template plus a prototype screenshot; do not commit smoke screenshots to the repository.

## Acceptance

- At 1500×837 with the same long-name template as production, the template column is about 240px and the fixed-combination column about 300px, and the row height drops from 332px to around 74px.
- Template name, description and fixed combination all show ellipses; hovering reveals the full name/description/combination; action buttons do not break word by word.
- Narrow screens scroll horizontally instead of squashing the fixed-combination column.
- The prototype shows the same 8-column structure and truncation with the same data; before/after screenshots use the same viewport and theme and are attached to the implementation PR.
- `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs` and the `web` build pass.

## References

- Implementation PR: #387 (review record: `plans/2026-11-23-pr387-review.md`).

# [m10] Eval quick-start row diverges from the prototype

English | [中文](m8-eval-quick-start-row.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - web/src/pages/EvalsPage.tsx
  - web/src/components/TemplateBits.tsx
  - web/src/styles/templates.css
  - plans/**
  - .agents/flow/issues/m8-eval-quick-start-row.md
  - .agents/flow/issues/m8-eval-quick-start-row.en.md
  - .agents/flow/issues/m8-eval-quick-start-row.i18n.yaml
adr: []
github:
  number: 388
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/388
```

## Background

The production "Evaluations / Quick start" area diverges visually from `prototype`: the template name is an antd blue link (12px / 650) while the prototype uses a dark 13px / 700 title; the last launch is vertical plain text "never launched" plus "—" while the prototype shows a neutral status tag with the time on the same row; the actions area has an extra "enabled" tag; row padding, separators and combo font size also differ. Long template names were clipped or overflowed on narrow viewports, and at 1200px they once painted over the adjacent column. The root cause is that M10 #378 rewrote the quick-start row in React without porting the prototype's `quickTemplateRow` and `.quick-template-*` styles.

## Goals

- Render the name as a prototype-style text button: dark 13px / 700, wrapping, primary color on hover, no antd blue link button.
- Render the last launch as a status tag plus the time on the same row; show a neutral "not launched yet" tag before the first run.
- Remove the "enabled" tag from the quick-start row and keep only "details / launch"; template status stays in the full list.
- Restore the prototype row styles: `padding: 11px 0`, list `0 19px 14px`, dashed separators, 10px combo line.
- Hide the last-launch column at `≤1240px` and keep the name wrapping or clear of the adjacent column.
- Add screenshots or DOM assertions at 1500px and 1200px with a long-name template (no overlap inside the row, last launch hidden at 1200px); do not commit screenshots to the repository.

## Acceptance

- At 1500×837 the quick-start row is about 60px high, the name is dark 13px / 700, the last launch is a tag plus time on one row, and the actions area has no "enabled" tag.
- At 1200×837 the last launch is hidden, the name does not cover the adjacent column, and there is no horizontal overlap.
- The prototype shows the same structure with the same data and viewport; before/after screenshots use the same viewport and theme and are attached to the implementation PR.
- `npm test`, `npm run verify:flow`, `npm run verify:docs` and the `web` build pass.

## References

- Implementation PR: #389 (review record: `plans/2026-11-23-pr389-review.md`).

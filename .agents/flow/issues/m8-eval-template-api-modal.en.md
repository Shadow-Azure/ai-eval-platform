# [m10] Template API dialog diverges from the prototype

English | [中文](m8-eval-template-api-modal.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - web/src/components/TemplateApiModal.tsx
  - web/src/pages/EvalTemplateDetailPage.tsx
  - web/src/pages/EvalTemplatesPage.tsx
  - web/src/styles/templates.css
  - plans/**
  - .agents/flow/issues/m8-eval-template-api-modal.md
  - .agents/flow/issues/m8-eval-template-api-modal.en.md
  - .agents/flow/issues/m8-eval-template-api-modal.i18n.yaml
adr: []
github:
  number: 391
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/391
```

## Background

The production "template API" dialog diverges from `prototype` in six places: the dialog is 952px tall, overflows the 837px viewport and has no footer; long curl lines push the code block and the whole item to 834px, past the 760px dialog; the summary is a single column of three rows; the GET/POST method tags are grey/blue instead of blue/green; and the subtitle repeats the authentication note. The root cause is that M10 #378 added `TemplateApiModal` in React without porting the prototype's `openTemplateApi`, `.modal.wide` and `.api-*` constraints.

## Goals

- Cap the dialog at `calc(100vh - 48px)`, scroll the body internally, and keep the title and footer visible.
- Add a "close / one-click launch" footer; the launch entry closes the API dialog and opens the launch dialog from the detail and list pages.
- Use `white-space: pre-wrap; overflow-wrap: anywhere` for code blocks and `min-width: 0` for grid items and path rows so long curl lines stay inside the dialog.
- Restore the prototype's three-column summary, falling back to one column on narrow viewports; use the prototype GET blue / POST green method tags.
- Keep only the template name and three execution endpoints in the subtitle; show authentication only in the summary row.
- Verify at 1500×837 and 1200×837 with same-viewport, same-theme before/after screenshots and DOM assertions; do not commit screenshots to the repository.

## Acceptance

- At 1500×837 the dialog fits inside the viewport (about 760×789, top=24), the body has `scrollWidth <= clientWidth`, and the footer is visible.
- At 1200×837 the dialog has no horizontal overflow, the summary still has three columns, and code blocks wrap inside the dialog.
- Method tags are GET blue and POST green; the subtitle no longer contains the authentication note; the summary has three columns.
- The footer "close" button closes the dialog and "one-click launch" opens the launch confirmation dialog.
- The prototype shows the same structure with the same data and viewport; before/after screenshots use the same viewport and theme and are attached to the implementation PR.
- `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs` and the `web` build pass.

## References

- Implementation PR: #393 (review record: `plans/2026-11-23-pr393-review.md`).

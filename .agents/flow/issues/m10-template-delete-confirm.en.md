# [m10] Show delete action and confirmation dialog for disabled evaluation templates

English | [中文](m10-template-delete-confirm.md)

```yaml flow
kind: issue
milestone: m10
priority: P2
status: ready
scope:
  - prototype/**
  - .agents/flow/issues/m10-template-delete-confirm.md
  - .agents/flow/issues/m10-template-delete-confirm.en.md
  - .agents/flow/issues/m10-template-delete-confirm.i18n.yaml
adr: []
github:
  number: 416
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/416
```

## Background

Feedback from 2026-11-23: in the production "evaluation templates" list, a disabled template still keeps a disabled "Start" button in its action area. The user wants the "Start" button replaced by "Delete" once the template is disabled; clicking it must open a confirmation dialog and delete the template only after confirmation.

A disabled template cannot be started, so the action slot should show the action that is actually available. Deletion is only offered from the disabled state, which avoids deleting an enabled template by mistake.

## Current state and gap

| Area | Current state | Gap |
|---|---|---|
| Production evaluation template list | Disabled rows show the "disabled" status, an enable action and a disabled "Start" button | No delete entry; the disabled start button occupies the action slot |
| Prototype evaluation template list | Disabled rows show the "disabled" status and a disabled "Start" button | No delete button or confirmation dialog; the action slot still shows a disabled start |
| Prototype template data | Disabled templates `tpl-010` and `tpl-017` exist, and a status filter is available | No delete action and no post-delete list state |

## Goals

- Evaluation template list: the "Start" button of a disabled template becomes a red "Delete" button; enabled templates keep "Start".
- Clicking "Delete" opens a confirmation dialog that says deletion is irreversible and past evaluations are retained; "Cancel" closes the dialog without deleting.
- Confirming removes the template from the list and shows a notice; removing the last template that matches the filter shows the empty state.
- The prototype lands first with before/after screenshots; the production implementation follows this issue.

## Acceptance

- The action area of a disabled row shows "Details / API / History / Delete" and no disabled start button.
- Clicking "Delete" opens the confirmation dialog; cancelling keeps the template, and confirming removes the row, decreases the count and shows a notice.
- Deleting only affects the template itself; launched evaluations and their traces are retained.
- Before and after screenshots use the same viewport, theme and data, and are attached to this issue and the prototype PR.
- `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs` and PR CI pass.

## Non-goals

- The prototype PR for this issue does not change production `web/` or `backend/`, and does not add a delete API.
- No bulk deletion, enable/disable toggling, or deletion of enabled templates.
- No change to template launch, evaluation execution, scheduling, scoring or trace links.

## References

- Related: issue #378 (evaluation templates), issue #394 (template launch history), issue #410 (template origin).
- Design: [docs/design/eval-platform-architecture.en.md](../../../docs/design/eval-platform-architecture.en.md) §5.14.

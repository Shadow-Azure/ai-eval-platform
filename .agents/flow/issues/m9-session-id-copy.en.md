# [m9] Copy the main Agent session id with one click

English | [中文](m9-session-id-copy.md)

```yaml flow
kind: issue
milestone: m9
priority: P0
status: done
scope:
  - web/src/**
  - tests/**
  - prototype/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 285
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/285
```

## Background

#277 added the main Agent session id to the case list, but the value is truncated to `8…4`; copying the full id still needs hover or manual selection. Add a copy button that copies the full native session id in one click.

## Goals

- Add a copy button next to cells that have a session id; copy the full native session id, not the truncated value or the attempt id.
- Copying must not trigger row navigation; `—` rows have no button; the button is keyboard focusable and has a clear accessible name.
- Show a short success/failure toast; update the prototype first with before/after screenshots, then the production frontend.

## Acceptance

- Prototype copy succeeds with a toast and no URL change; rows without a session have no button.
- Production frontend copies the full `main_session_id` with a failure fallback; filters, pagination, row click and trace entry do not regress.
- No backend change; frontend build, `npm test`, `verify:docs`, `verify:flow` pass; before/after screenshots are attached to issue #285.

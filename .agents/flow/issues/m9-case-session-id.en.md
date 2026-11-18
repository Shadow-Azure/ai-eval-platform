# [m9] Show main Agent session id in the case list

English | [中文](m9-case-session-id.md)

```yaml flow
kind: issue
milestone: m9
priority: P2
status: ready
scope:
  - backend/app/**
  - tests/**
  - web/src/**
  - prototype/**
  - docs/**
  - plans/**
  - scripts/**
  - .agents/flow/**
adr: []
github:
  number: 277
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/277
```

## Background

The evaluation detail "cases" table shows execution, result, trace, test summary and duration, but no main Agent session id. Investigating bad case reports and harness-evolution backlinks requires opening each trace page to find the native session id, so the list cannot be compared with HE `sessionId` directly. `trace_session` already stores the root session `native_session_id` per attempt; cases without a trace have no native session. Before/after prototype screenshots are attached to issue #277.

## Goals

- Add a "main Agent session id" column to the case list showing the latest attempt's main Agent native session id; show `—` when absent.
- Return the main Agent session id from the case list API in a batch per page, without N+1 queries.
- Truncate long values and expose the full value on hover; keep existing filters, pagination, CSV export, row click and trace link unchanged.
- Update the API and architecture current-state docs, and add backend and frontend tests.

## Acceptance

- Each case row returns the main Agent session id (suggested `main_session_id`) equal to the latest attempt root session `native_session_id`; `null` when there is no trace or it is unresolved.
- The frontend column renders the truncated value and full-value hint, `—` empty state, horizontal scroll on narrow screens, and no regression to existing interactions.
- Prototype matches the production page semantics; before/after prototype screenshots are attached to issue #277.
- `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs` and the relevant backend tests pass.

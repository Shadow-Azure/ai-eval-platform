# [m8-422] Render a readable timeline in the evaluation detail live events card

English | [中文](m8-422-eval-event-timeline.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-422-eval-event-timeline.*
  - plans/**
  - tests/eval-event-timeline.test.mjs
  - web/src/features/evaluations/eventTimeline.ts
  - web/src/pages/EvalDetailPage.tsx
  - web/src/styles/eval-detail.css
adr: []
github:
  number: 422
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/422
```

## Background

Production "evaluation detail → live events" renders the `files` object list of `trace.ingestion.updated` directly in the card: `eventSummary()` has no mapping for that event type, falls into the default branch that joins the first three payload keys as `key=value`, and PostgreSQL JSONB does not guarantee key order, so a whole JSON blob fills the card. The overview also takes only the newest 6 rows, so "trace ingestion" snapshots and heartbeats push case start, completion, failure and dispatch events out of view.

## Goals

- Live events show only human-readable summaries; long payloads collapse into counts or a single line and never render `files=` or object arrays.
- `trace.ingestion.updated` maps to "trace ingestion: N files ready / pending / failed" and keeps trace status and attempt context.
- The overview timeline matches the prototype: event subject + action + one context line + time on the right, with dots and connecting lines.
- The overview selects key dispatch / case events, keeps only the newest ingestion snapshot per attempt, and never spends overview slots on heartbeats.
- The full event log behind "view all" still shows every event, with the same readable summaries.

## Plan

- Add `web/src/features/evaluations/eventTimeline.ts` to centralize event labels, single-event summary projection and overview timeline selection; unknown types read scalar fields only and never serialize nested objects.
- `trace.ingestion.updated` reads `expected`, `ready`, `pending`, `degraded` and `failed` from `counts` / `file_counts`; the action line prints counts and the context line prints trace status, attempt and error summary.
- The overview walks events from newest to oldest over an allowlist of key events and deduplicates ingestion snapshots by `(case_run_id, attempt_no)`; with lifecycle rows present it keeps at most 2 snapshots, otherwise snapshots fill the list.
- The `EvalDetailPage` overview card becomes a timeline (dot, connecting line, subject, context, `HH:MM`), and the event log table reuses the same summaries; styles follow the prototype `timeline-item` grid and dot size.
- Add Node tests covering ingestion summaries, unknown payloads, timeline selection and time formatting.

## Acceptance

- A completed evaluation containing `trace.ingestion.updated` shows no `files=` or whole JSON in the live events card.
- The overview shows case / shard summaries, context and time, and repeated ingestion snapshots do not consume all 6 slots.
- "View all" still shows the full event log.
- At 1500×837 and 1200×837 the card has no horizontal scroll and each event keeps a stable height.
- `npm test`, `npm run verify:flow`, `npm run verify:docs` and `web npm run build` pass.

## Scope

- Files: evaluation event summary mapping, overview timeline markup and styles, frontend regression tests, flow issue entry.
- Out of scope: event API, backend event writes, event stream connection logic, case detail and results pages.

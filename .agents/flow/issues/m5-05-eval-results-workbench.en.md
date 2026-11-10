# Complete the evaluation detail and results workbench

English | [中文](m5-05-eval-results-workbench.md)

```yaml flow
kind: issue
milestone: m5
priority: P0
status: done
scope:
  - .agents/flow/issues/m5-05-eval-results-workbench.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
  - plans/**
adr: []
github:
  number: 87
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/87
```

## Background

The current evaluation detail page exposes status, result cards, a case table, a test-detail drawer, and an event timeline, but lacks structured sections, filtering, configuration snapshots, failed-case retry, budget resume, and large-log viewing. As M4 adds concurrency, timeouts, cancellation, and budget pause, this page must become the single workbench for explaining results and taking recovery actions.

## Goals

- Reorganize evaluation detail into clear overview, cases, tests and results, configuration snapshot, and event-log sections while preserving URL state and deep-link recovery.
- Support case search and filtering by execution, result, and trace state, and show attempt history, failure summaries, test logs, and object links.
- Add cancellation, failed-case retry, and budget-pause display and resume actions, keeping UI state consistent through SSE or refresh after each action.
- Show metric definitions, denominators, finality, concurrency, timeout, retry, cost, and resource-pool snapshots, with safe fallback for unknown metric types.

## Acceptance

- Refreshing or reconnecting during execution loses no progress and never regresses state; terminal evaluations stop unnecessary reconnects, and events from older evaluations never enter the current detail.
- A user can filter and open one case to see its latest and historical attempts, test-level outcomes, and authorized failure logs.
- Cancel, retry, and budget-resume actions handle permissions, confirmation, errors, and concurrent state, with results traceable in the detail view and event log.
- Tests or visual comparison evidence cover key interactions, state sections, empty and error states, permissions, and metric fallback.

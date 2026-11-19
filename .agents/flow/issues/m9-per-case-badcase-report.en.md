# Report Each Bad Case as Soon as Its Case Settles

English | [中文](m9-per-case-badcase-report.md)

```yaml flow
kind: issue
milestone: m9
priority: P0
status: in-progress
scope:
  - backend/app/**
  - tests/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 288
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/288
```

## Background

Bad cases are currently enqueued only when the evaluation run reaches a terminal state, so in a long evaluation the first finished bad cases wait for the last case before reaching harness-evolution and the feedback loop is delayed as a whole; `self_evolution_report` is already an outbox and HE accepts a single bad case at any time, so enqueueing can happen per case once its result is immutable.

## Goals

- Add a per-case reconciliation pass to the worker after each trace-ingestion publication; enqueue `succeeded` non-resolved cases immediately and enqueue `failed`/`timeout` cases only after their retry budget is exhausted.
- Process only cases whose trace status is terminal and whose final attempt registered ingestible trace objects; skip pending traces, trace-less cases, resolved cases, and cancelling/cancelled runs.
- Reset `trace_status` to pending whenever a retry creates a new attempt, so a failed retry cannot reuse the previous attempt's terminal badge and be reported early.
- Keep the existing terminal batch enqueue as the fallback; keep enqueue idempotent through the unique key and savepoints.
- Align the architecture document and the fullstack design reporting timing, and add unit coverage.

## Acceptance

- In a running run, one `succeeded + unresolved` case produces a pending row after the worker pass and can be POSTed in the same tick.
- A `failed` case is not enqueued before its attempts are exhausted and is enqueued afterwards; `succeeded + resolved`, `cancelled` cases, `cancelled/cancelling` runs, and trace-less cases are not enqueued.
- A `trace_status=pending` case waits; after trace reaches terminal it is enqueued with `sessionSource` when configured. After an automatic retry `trace_status=pending`; when the final attempt has no trace it is corrected to unlinked and not reported.
- Repeated calls create one row; the existing `tests/backend/test_self_evolution_report.py` stays green and covers the cases above.
- `docs/architecture.md` and designs 04/05 describe "per-case enqueue after the result is immutable + terminal-run fallback"; related gates pass.

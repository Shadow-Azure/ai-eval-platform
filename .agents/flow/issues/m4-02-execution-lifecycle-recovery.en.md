# Complete execution timeouts, cancellation, and reconciliation recovery

English | [中文](m4-02-execution-lifecycle-recovery.md)

```yaml flow
kind: issue
milestone: m4
priority: P0
status: ready
scope:
  - .agents/flow/issues/m4-02-execution-lifecycle-recovery.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - runner/**
  - runner-contract/**
  - .github/workflows/**
  - tests/**
  - docs/**
  - deploy/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 79
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/79
```

## Background

M2 and M3 already provide a case-callback state machine, short-lived tokens, retries through new attempts, and outbox lease recovery. Cancellation still mostly changes database state, while execution timeouts, upload grace periods, remote GitHub job termination, and cross-system reconciliation remain incomplete. A disconnected runner or lost callback can leave a case stuck in queued or running indefinitely.

## Goals

- Define five timeout classes for queueing, agent execution, scoring, upload grace, and heartbeat, with stable error codes and diagnosable terminal states for each.
- On cancellation, stop new dispatch, revoke task and model tokens, and request cancellation of the GitHub workflow or job; late callbacks remain audit evidence and never overwrite the cancelled fact.
- Add a reconciler that combines GitHub run/job state, heartbeats, and uploaded artifacts to recover missing completion events or close disconnected attempts.
- Classify failures so environment and transport faults can retry, configuration errors fail fast, and scoring-only failures avoid rerunning the agent unnecessarily.

## Acceptance

- Boundary tests cover all five timeout classes, and timeouts, lost heartbeats, and missing objects cannot leave a case queued or running indefinitely.
- No new dispatch or model call starts after cancellation; when remote cancellation fails, the platform still preserves a cancelling or cancelled fact with retryable diagnostics.
- Late callbacks from old attempts return a stale result with audit retained, while worker restart, outbox replay, and lost-callback scenarios recover automatically.
- Retries preserve prior attempts, traces, and costs, and old callbacks cannot overwrite the latest valid result.

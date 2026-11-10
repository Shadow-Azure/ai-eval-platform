# Implement model budget pause and resume

English | [中文](m4-05-budget-pause-resume.md)

```yaml flow
kind: issue
milestone: m4
priority: P0
status: ready
scope:
  - .agents/flow/issues/m4-05-budget-pause-resume.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - web/**
  - tests/**
  - reports/**
  - plans/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 82
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/82
```

## Background

The model gateway already accumulates evaluation cost and rejects new requests after the budget is exhausted, but the evaluation state machine does not pause, the scheduler can still dispatch new cases, and users cannot add budget to continue. Once concurrent evaluation is enabled, returning only a 402 from the gateway would mix incomplete work with failed and queued states.

## Goals

- Maintain a budget version and authoritative spend per evaluation so the gateway can trigger an evaluation-level pause rather than only a single-request error.
- Implement the `pausing_budget` to `paused_budget` flow by stopping new dispatch and model calls while active attempts upload existing artifacts and exit safely.
- Add a resume API that atomically increases budget and version, writes an audit record, and reschedules only incomplete cases.
- During pause, do not generate final metrics or switch model or resources automatically; completed results and costs remain unchanged.

## Acceptance

- After reaching the budget, no new model call or new shard dispatch starts, while active cases can finish uploads and record `pause_reason=budget_exceeded`.
- Once all active attempts settle, the evaluation enters paused_budget, and the API exposes budget, spend, completed count, and incomplete count.
- Concurrent or repeated resume requests are idempotent, a mismatched budget_version is rejected, and resume creates new attempts only for incomplete cases.
- Automated tests cover pause, resume, out-of-range values, concurrent confirmation, and gateway rejection, and final metrics are generated only after the evaluation actually finishes or resumes.

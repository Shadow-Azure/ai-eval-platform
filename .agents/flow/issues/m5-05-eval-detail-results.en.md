# Build evaluation detail, live progress and result comparison

English | [中文](m5-05-eval-detail-results.md)

```yaml flow
kind: issue
milestone: m5
priority: P0
status: backlog
scope:
  - .agents/flow/**
  - web/**
  - backend/**
  - tests/**
  - package.json
  - package-lock.json
  - docs/**
  - plans/**
  - reports/**
adr: []
github:
  number: 37
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/37
```

## Background

After an evaluation is created, users need live progress, case status, test detail, metric semantics and baseline comparison instead of waiting for a single final number.

## Goals

- Build the detail page status header, stacked progress, metric cards, budget pause banner and cancel and retry actions.
- Build five tabs: case table, test detail, results, configuration snapshot and event timeline.
- Implement automatic baseline recommendation and manual comparison showing the reason and configuration differences.
- Wire SSE incremental refresh and reconnect recovery so refreshing during a run loses no progress.

## Acceptance

- Refreshing or reconnecting during a run loses no progress, and reconnection stops at the terminal state.
- Metric cards show formula, denominator and is_final, and a paused budget exposes a resume entry.
- Cases filter by execution status, result status and trace status, and test-level results and failure logs are visible.
- Comparison shows expected metric deltas and configuration differences, and unknown renderers degrade safely.

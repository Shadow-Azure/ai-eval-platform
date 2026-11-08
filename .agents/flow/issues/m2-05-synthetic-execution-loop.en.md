# Complete the synthetic evaluation dispatch, execution, callback, and state-persistence loop

English | [中文](m2-05-synthetic-execution-loop.md)

```yaml flow
kind: issue
milestone: m2
priority: P0
status: done
scope:
  - .agents/flow/issues/m2-05-synthetic-execution-loop.*
  - .agents/flow/milestones/m2-executable-foundation.*
  - backend/**
  - web/**
  - runner/**
  - runner-contract/**
  - tests/**
  - .github/workflows/**
  - deploy/docker-compose.prod.yml
  - reports/**
  - plans/**
adr: []
github:
  number: 15
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/15
```

## Background

M2's final evidence is not that individual components pass, but that a synthetic evaluation travels from platform creation through GitHub dispatch to the fixed runner and persists state through OSS and callbacks. Only after this path is real should M3 integrate models and datasets.

## Goals

- Provide minimal APIs to create, query, read events for, and cancel a synthetic evaluation.
- Use a transactional outbox to create shard dispatches, call repository_dispatch, and persist reconciliation data.
- Validate attempt and idempotency keys before accepting started, heartbeat, completed, and failed callbacks and updating state.
- Provide a minimal web or API view showing the synthetic evaluation from queued to terminal state and covering restart recovery.

## Acceptance

- One synthetic evaluation executes through the real GitHub workflow on the fixed Linux runner and writes its result object and terminal state back to PostgreSQL.
- Duplicate and out-of-order callbacks and worker restarts create no duplicate attempt and never regress state.
- Induced dispatch failure, runner failure, and missing objects reach diagnosable failure states rather than remaining queued forever.
- The complete path runs repeatedly without calling any model and produces contracts and fixtures reusable by M3.

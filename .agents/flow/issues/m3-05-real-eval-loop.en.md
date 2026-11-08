# Complete the end-to-end loop for one real evaluation case

English | [中文](m3-05-real-eval-loop.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: ready
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
  - runner/**
  - runner-contract/**
  - .github/workflows/**
adr: []
github:
  number: 27
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/27
```

## Background

After datasets, the model gateway, runtime, scoring and callbacks are built separately, the platform must prove they form a reproducible evaluation loop on one real case instead of only passing unit tests in isolation.

## Goals

- Provide minimal evaluation creation and query APIs that freeze dataset version, runtime, assets, model, resource pool and metric version snapshots.
- Run one SWE-bench case through a real model endpoint (MiniMax or compatible) covering agent execution, official scoring and result lookup.
- Reuse the M2 dispatch, callback and idempotency contracts, filling in the real task manifest and callbacks.
- Establish a repeatable end-to-end fixture and verification report, keeping regular CI free of real model calls.

## Acceptance

- A real case reaches a successful terminal state, and its result, test detail and failure logs are queryable.
- Invalid credentials, scoring failure and lost callbacks are diagnosable and never create duplicate attempts.
- Repeating creation with the same idempotency key returns the same evaluation, and configuration changes are rejected explicitly.
- Real model calls run only in a controlled environment; regular CI passes with mocks or fixtures.

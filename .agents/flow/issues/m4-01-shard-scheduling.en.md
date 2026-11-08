# Add shard scheduling, concurrency control and multi-runner dispatch

English | [中文](m4-01-shard-scheduling.md)

```yaml flow
kind: issue
milestone: m4
priority: P0
status: backlog
scope:
  - .agents/flow/**
  - backend/**
  - runner/**
  - runner-contract/**
  - tests/**
  - deploy/**
  - .github/workflows/**
  - docs/**
  - plans/**
  - reports/**
  - pyproject.toml
  - docker-compose.yml
adr: []
github:
  number: 28
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/28
```

## Background

The single-case loop cannot use multiple runners. M4 must split evaluations into bounded shards, dispatch by online capacity and requested concurrency, and keep duplicate dispatches and out-of-order callbacks from corrupting state.

## Goals

- Implement an outbox dispatcher worker that builds shard manifests, calls repository_dispatch and stores dispatch_key reconciliation data.
- Shard with a size of 16 to 64 and a hard limit of 256, computing effective concurrency from online runners, pool limits and requested concurrency.
- Let multiple ephemeral runners claim and execute jobs concurrently, aggregating the run into completed, partial or failed.
- Keep cases queued when capacity is short and publish explicit events such as resource_price_above_threshold.

## Acceptance

- N online runners execute N case jobs in parallel while excess cases stay queued.
- Duplicate dispatches, duplicate callbacks and out-of-order callbacks create no duplicate attempts and never move state backwards.
- 500 cases split into no more than 8 shards, and each job still executes exactly one case.
- Failed shards can be retried, reconciliation data is queryable, and the dispatcher recovers from the outbox after restart.

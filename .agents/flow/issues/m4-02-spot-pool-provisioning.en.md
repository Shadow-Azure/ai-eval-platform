# Provision and reclaim the Aliyun spot resource pool

English | [中文](m4-02-spot-pool-provisioning.md)

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
  number: 29
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/29
```

## Background

A fixed runner only proves the loop; elastic evaluation needs Aliyun spot instances created from the queue gap, ephemeral runners registered, and safe reclamation when idle or interrupted.

## Goals

- Add a connection table and API that validates ECS provisioning capability, and keep pool minimum, maximum and instance configuration.
- Implement spot ECS provisioning: price threshold, backoff for inventory and creation failures, JIT ephemeral runner registration, idle scale-in and interruption reclamation.
- Drive scaling from both workflow_job events and periodic queue queries, so a lost webhook never prevents scale-out.
- Inject one-time registration data only during instance initialization, never long-lived cloud credentials or model keys.

## Acceptance

- A pool scales to its limit and registers ephemeral runners, deregistering before releasing instances when idle.
- When the current price exceeds the threshold no instance is created, the evaluation stays queued and the price, threshold and next check time are shown.
- Spot interruption and lost heartbeats are marked and retried without producing duplicate results.
- Creation failure, insufficient inventory and release errors are covered by mock client tests.

# Integrate Aliyun spot ECS and ephemeral runners

English | [中文](m4-04-aliyun-spot-provisioner.md)

```yaml flow
kind: issue
milestone: m4
priority: P0
status: done
scope:
  - .agents/flow/issues/m4-04-aliyun-spot-provisioner.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - runner/**
  - infra/**
  - deploy/**
  - .github/workflows/**
  - tests/**
  - docs/**
  - plans/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 81
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/81
```

## Background

Fixed Linux runners validate the execution path but cannot scale elastically with queued work. The target architecture requires Aliyun spot ECS instances to provide ephemeral runners, but the repository lacks price and inventory queries, instance lifecycle management, one-time runner registration, and interruption recovery. Idle capacity also cannot scale down automatically.

## Goals

- Implement an Aliyun ECS spot provisioner covering price and inventory queries, instance creation, boot waiting, release, and interruption detection.
- Add one-time registration for ephemeral runners so registration material exists only during instance initialization and each runner binds to the pool labels and a unique name.
- Connect the provisioner to the M4-03 capacity controller to scale up in batches and unregister runners before releasing instances when no active task remains.
- Route spot interruption, lost heartbeats, creation timeout, and insufficient inventory through reconciliation and retry without duplicate results.

## Acceptance

- A controlled cloud environment can create one spot instance, register an ephemeral runner, execute a small batch, and complete scale-down plus release after the tasks finish.
- When price exceeds the threshold, inventory is insufficient, or creation fails, cases are not marked failed; the evaluation remains queued with diagnosable resource-pool reasons.
- Spot interruption or an unexpected runner disconnect identifies and retries the affected attempt without overwriting prior results.
- Long-lived cloud credentials, model keys, and one-time registration material never enter logs, artifacts, or persistent runner disks, with provider mocks and a real smoke report as evidence.

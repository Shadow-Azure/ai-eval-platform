# Implement case-level matrix and effective concurrency scheduling

English | [中文](m4-01-case-matrix-concurrency.md)

```yaml flow
kind: issue
milestone: m4
priority: P0
status: done
scope:
  - .agents/flow/issues/m4-01-case-matrix-concurrency.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - runner/**
  - runner-contract/**
  - .github/workflows/**
  - tests/**
  - docs/**
  - plans/**
  - reports/**
  - deploy/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 78
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/78
```

## Background

M3 proved that one real case can complete execution, scoring, and callback delivery, but the current workflow still runs an entire shard sequentially in one job. Different shards can be claimed by different runners, yet concurrency, queueing, and failure isolation remain too coarse at the case level. Evaluation creation also hard-codes `requested_concurrency` to 1, so preflight results do not reach real scheduling.

## Goals

- Keep one workflow run per shard and use a workflow matrix so every job executes exactly one case.
- Define `requested_concurrency` as the hard cap on simultaneously running cases in one evaluation and derive effective concurrency from it, idle runners, and resource-pool capacity.
- Gate shard and case release in the scheduler so work stays queued without capacity and GitHub queueing is not the only throttle.
- Preserve the existing manifest, short-lived token, atomic claim, and single-case retry contract, while duplicate jobs exit before starting an agent.

## Acceptance

- Five hundred cases with the default shard size of 64 produce eight workflow dispatches, and every matrix job contains exactly one case.
- With at least two online runners, the number of running cases reaches the effective concurrency cap, and excess cases remain queued without duplicate attempts.
- Duplicate dispatch, worker restart, and replay of the same job do not duplicate execution or uploads, nor regress run state.
- Backend state-machine tests, workflow structure tests, the runner case entry point, and at least one multi-runner integration verification provide evidence.

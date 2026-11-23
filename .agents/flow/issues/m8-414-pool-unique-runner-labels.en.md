# [m8-414] Unique per-pool Runner labels and dispatch isolation

English | [中文](m8-414-pool-unique-runner-labels.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-414-pool-unique-runner-labels.*
  - backend/**
  - docs/**
  - infra/runner/bootstrap.sh
  - plans/**
  - reports/**
  - tests/**
  - web/src/api/resources.ts
  - web/src/pages/ResourcesPage.tsx
adr: []
github:
  number: 414
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/414
```

## Background

The platform uses resource pools as logical isolation units, but GitHub Actions only matches Runner labels. The fixed pool kept the global `self-hosted,linux,eval` labels, and every spot Runner owned those three labels plus its pool label, so fixed jobs could run on spot Runners: fixed evaluations consumed spot capacity, spot evaluations queued instead; the page and run snapshot still showed the fixed pool and capacity accounting was wrong.

## Goals

- Every pool owns an explicit, unique `runs_on` label set shared by registration and dispatch.
- A pool never falls back to the global default labels; missing labels are rejected on create, update and launch preflight.
- The migration backfills unique labels for historical `{"kind": "fixed"}` pools; the fixed-runner bootstrap registers the pool-unique label by default.
- A regression test proves fixed job labels and spot Runner labels are not subsets.

## Plan

- `runner_labels` accepts only a pool's explicit labels: a pool without `runs_on` raises `RunnerLabelsMissing`, and only a synthetic run without a pool uses the three defaults; the `LEGACY_FIXED_LABELS` fallback is removed.
- Resource-pool create/update require a non-empty `runs_on` containing `self-hosted` and at least one non-default pool-unique label, and reject a subset or superset relation with another pool.
- Labels are stored once as `labels_json.runs_on`; console label maps and `instance_config.runs_on` are normalized on save.
- Migration 0034 backfills `runs_on` for historical pools, derives a unique label from the pool name and appends an id-derived label when needed to break overlap.
- `ensure_fixed_pool` writes `self-hosted,linux,eval,pool-fixed-linux`; the fixed-runner bootstrap appends `pool-fixed-linux` by default, effective on re-registration.
- Launch preflight checks missing pool labels, online Runners missing the pool label, and overlap with another pool; a fixed pool with no compatible Runner blocks, while stale spot Runners only warn.
- The resource-pool form makes Runner labels required, and the list/detail views render labels from `runs_on`.

## Acceptance

- A fixed-pool evaluation's execute job matches only that pool's Runner and never any spot Runner.
- A spot-pool evaluation's execute job matches only spot-pool Runners.
- Creating/updating a pool without a unique `runs_on`, or with an overlapping set, returns 422; migrated historical pools have unique labels.
- The fixed/spot subset regression passes after the fix; `pytest tests/backend`, `npm test`, `npm run verify:flow`, and `npm run verify:docs` pass.

## Scope

- Files: resource-pool label resolution and dispatch, resource-pool API validation, launch preflight, Alembic migration, fixed-runner bootstrap, resource-pool label rendering and form, tests and architecture docs.
- Out of scope: `effective_concurrency=0` caused by cross-evaluation `pool_in_flight` reservation, the spot provisioner implementation, and Runner execution logic.

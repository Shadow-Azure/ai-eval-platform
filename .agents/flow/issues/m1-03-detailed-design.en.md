# Freeze the M2 executable foundation contracts

English | [中文](m1-03-detailed-design.md)

```yaml flow
kind: issue
milestone: m1
priority: P0
status: ready
scope:
  - .agents/flow/**
  - .agents/flow/issues/m1-03-detailed-design.*
  - .agents/flow/issues/m1-01-eval-architecture.*
  - .agents/flow/issues/m1-eval-architecture.*
  - docs/architecture.*
  - docs/design/eval-platform-detailed-design.*
  - docs/README.*
  - contracts/**
  - plans/m1-02-fullstack-design/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 5
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/5
```

## Background

`plans/m1-02-fullstack-design/` is now the implementation baseline for the evaluation platform, and a complete detailed design for the entire platform is no longer an M2 prerequisite. M1-03 freezes only the contracts needed for the M2 control plane, database, deployment, fixed runner, and synthetic execution loop so the five M2 issues can be implemented and accepted directly; designs for M3 and later evolve in later milestones using implementation evidence.

## Goals

- Map M2 services, modules, code paths, and dependency direction, and define the minimal boundaries among the platform, dispatcher, fixed runner, PostgreSQL, Redis, and object storage.
- Freeze the initial M2 data model, migration order, composite tenant constraints, and idempotency keys for evaluations, cases, attempts, events, and outbox records.
- Freeze machine-readable contracts and state transitions for M2 APIs, SSE queries, task manifests, heartbeats, uploads, completion, and failure callbacks.
- Freeze the minimal operating flow for main-branch migration and deployment, health checks, compatible rollback, and fixed Linux runner registration and loss recovery.
- Map contracts to acceptance for M2-01 through M2-05, while explicitly leaving OIDC, spot provisioning, multiple runners, and real agents to M3 or M4.

## Acceptance

- The M2 execution contracts land as a Chinese base, an English `.en.md`, and an `.i18n.yaml` trio, and are listed in the documentation index.
- `contracts/` provides an M2 OpenAPI summary or interface schema, task and callback JSON schemas, and the actual migration order and deployment state machine.
- Every M2 issue traces its acceptance items to a baseline document section, database objects, API or task protocol, and corresponding automated test.
- Baseline items marked as recommendations, defaults, or requiring technical validation that affect M2 receive a frozen value, validation task, or explicit later milestone, with no M3+ open item blocking M2.
- The contracts pass repository gates and technical review; this issue contains no production code, and M2-01 can enter implementation after review.

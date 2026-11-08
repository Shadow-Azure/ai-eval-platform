# Freeze the M2 execution contracts

English | [中文](m1-03-detailed-design.md)

```yaml flow
kind: issue
milestone: m1
priority: P0
status: done
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

`plans/m1-02-fullstack-design/` is design input for M1-03, not the formal detailed design or an implementation baseline. M1-03 does not revisit the overall architecture or technology choices; it turns only the parts genuinely needed by M2 into executable contracts so the five M2 issues can be implemented, tested, and accepted directly. Designs for M3 and later evolve in later milestones using implementation evidence.

## Goals

- Use the design input as a starting point to freeze the initial M2 database schema, core table relationships, tenant and idempotency constraints, and actual migration order.
- Freeze the M2 API skeleton, runner task protocol, dispatch and callback contracts, and their state transitions and error classification.
- Freeze the M2 deployment and migration flow, health checks, compatible rollback boundary, and minimal fixed Linux runner connection path.
- Promote recommendation defaults affecting M2 into frozen values or explicit technical validation items, and map M2-01 through M2-05 to concrete contracts and acceptance tests.

## Acceptance

- Produce one concise formal design as a Chinese base, an English `.en.md`, and an `.i18n.yaml` trio listed in the documentation index, without mechanically translating all 11 design-input documents.
- `contracts/` provides machine-readable contracts for the M2 API skeleton, task and callback schemas, database migration order, deployment state machine, and related interfaces.
- The formal design includes a traceability matrix for M2-01 through M2-05, mapping every acceptance item to a design section, database object, API or protocol, and automated test.
- Recommendations, defaults, and technical-validation items in the design input form a frozen-decision ledger marked as a frozen value, M2 validation task, or later milestone; items not affecting M2 must not block implementation.
- The contracts pass repository gates and technical review; this issue contains no production code, and M2-01 can enter implementation after review.

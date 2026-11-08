# Write the evaluation platform detailed design document

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
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 5
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/5
```

## Background

The finalized architecture answers what the system does and which major technologies it uses, but it does not yet expand service boundaries, database tables, API contracts, state machines, task protocols, and testing strategy to an implementable level. Before development, the team needs an engineering baseline consistent with the architecture and prototype to split implementation work and review code.

## Goals

- Define service and module boundaries, code organization, and dependency direction, along with the responsibilities of the platform, dispatcher, runner, object storage, and GitHub Actions.
- Specify the MVP data model, migration strategy, indexes, and multi-tenant constraints, and define the core APIs, events, error codes, and SSE protocol.
- Detail the evaluation and case state machines, concurrent scheduling, retry idempotency, timeout recovery, callback authentication, and short-lived credential flow.
- Define the five-file task package, dataset and execution adapter interfaces, normalized session trace model, and OSS directory conventions.
- Cover frontend routes and component boundaries, test layers, observability, security boundaries, deployment rollback, and the CI/CD implementation.

## Acceptance

- The detailed design lands as a Chinese base, an English `.en.md`, and an `.i18n.yaml` trio, and is listed in the documentation index.
- Every MVP primary flow is traceable through explicit page, API, service, database, task package, and storage contracts.
- Module diagrams, the data model diagram, key sequence diagrams, and state machines are complete, and API examples cover success, failure, authentication, and idempotency scenarios.
- The design gives an executable migration order, test matrix, failure handling, and production rollback steps, and marks every open decision with a default option.
- The document passes repository gates and technical review and can be split directly into implementation issues; this issue does not include production code.

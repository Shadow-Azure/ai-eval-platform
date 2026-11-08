# Finalize evaluation platform architecture

English | [中文](m1-01-eval-architecture.md)

```yaml flow
kind: issue
milestone: m1
priority: P0
status: done
bootstrap: true
scope:
  - .agents/flow/issues/m1-01-eval-architecture.*
  - docs/**
  - research/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 3
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/3
```

## Background

The evaluation platform has eleven requirements (login, datasets, model credentials, resource pool, evaluation launch, multi-tenant reservation, live progress, session traces, frontend trace viewer, concurrency, CI/CD deployment); the architecture must be finalized before development starts.

## Goals

- Complete the industry survey of open-source solutions and mechanisms (evaluation frameworks, session trace storage, GitHub Actions resource pools).
- Produce candidate architectures, discuss them with the user, and finalize the execution layer, backend stack, trace UI, and MVP scope.

## Acceptance

- The bilingual trio of `docs/design/eval-platform-architecture.md` lands and passes all documentation gates.
- The HTML architecture document ships with the trio and matches the Markdown version.
- Working research notes live in `research/` and `reports/`; the formal document records only finalized decisions.

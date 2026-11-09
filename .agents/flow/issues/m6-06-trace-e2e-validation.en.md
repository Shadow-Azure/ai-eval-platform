# Validate real parent-child agent traces end to end and under degradation

English | [中文](m6-06-trace-e2e-validation.md)

```yaml flow
kind: issue
milestone: m6
priority: P0
status: ready
scope:
  - .agents/flow/issues/m6-06-trace-e2e-validation.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - backend/**
  - runner/**
  - runner-contract/**
  - web/**
  - tests/**
  - reports/**
  - docs/**
  - .github/workflows/eval-shard.yml
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 95
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/95
```

## Background

The M6 loop must prove that real Claude Code native files flow through the runner, OSS, importer, API, and web UI rather than relying only on synthetic fixtures. It must also show that retries, duplicate callbacks, format upgrades, and authorization boundaries preserve historical attempts and scoring results.

## Goals

- Execute a real case with a root agent and multiple subagents on the fixed runner, verifying multi-file upload, normalization, session tree, waterfall, span details, and controlled raw download end to end.
- Verify that failed retries create independent attempts, duplicate upload/completion callbacks and imports remain idempotent, and parser upgrades replay originals without losing historical traceability.
- Verify degraded display for missing child files, unknown formats, and historical stdout-only data without inventing parent-child relationships or changing execution and scoring terminal states.
- Cover cross-tenant access, raw-file authorization and auditing, malicious-content safety, and sensitive-data checks, producing real-path tests and smoke evidence.

## Acceptance

- A real parent-child case links from case detail to its selected attempt, displays session relationships, span attributes, and final usage consistent with native evidence, and lets authorized users download the corresponding originals.
- Retried attempts keep isolated traces, duplicate callbacks do not add sessions or spans, and parser replay creates neither duplicate derived data nor overwritten originals.
- Missing files, unknown formats, and historical stdout-only data expose explicit degradation and raw access, scoring remains available, and the system never claims to recover absent native relationships.
- Authorization, audit, XSS, and credential-leak checks pass, while the test report records the environment, runtime/parser versions, evidence, and every uncovered item accurately.

# Build versioned trace models and an idempotent ingestion pipeline

English | [中文](m6-02-trace-ingestion-model.md)

```yaml flow
kind: issue
milestone: m6
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m6-02-trace-ingestion-model.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - backend/**
  - tests/**
  - plans/**
  - reports/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 91
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/91
```

## Background

The control plane already stores attempt-level object manifests and `trace_status`, but it has no `trace_file/session/span` models or ingestion pipeline that reads OSS originals and persists derived traces. M6 must make parsing replayable and failures degradable without blocking completed scoring results.

## Goals

- Add tenant- and attempt-scoped `trace_file/session/span` tables for native identifiers, parent evidence, runtime/format/parser versions, ordering, timing, usage, previews, and large-content object references.
- Trigger asynchronous ingestion after completion, verify object size and sha256, parse through the selected runtime adapter, and replace the corresponding derived tree in one transaction.
- Make repeated callbacks idempotent by file digest and parser_version, and allow an upgraded parser to replay the same OSS original without duplicating sessions or spans.
- Expose queryable ready, degraded, and failed states with redacted error summaries and trace-change events; isolate file or record failures from other files and scoring results.

## Acceptance

- Reimporting the same file and parser_version leaves record counts unchanged, while replaying an upgraded parser replaces the derived tree completely and preserves the original and version metadata.
- Missing child files, unknown formats, or malformed lines produce explicit degradation with file/line summaries; other parsed traces remain queryable and sensitive source lines never enter error logs.
- Ingestion failure does not change execution or scoring terminal states, and trace-state changes emit events that let the frontend refresh.
- Migrations run on an empty database and the previous version, with backend tests for rollback, concurrent duplicate ingestion, object verification, and replay.

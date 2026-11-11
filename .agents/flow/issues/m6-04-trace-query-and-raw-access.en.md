# Provide trace query APIs and controlled raw-file access

English | [中文](m6-04-trace-query-and-raw-access.md)

```yaml flow
kind: issue
milestone: m6
priority: P0
status: done
scope:
  - .agents/flow/issues/m6-04-trace-query-and-raw-access.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - backend/**
  - tests/**
  - plans/**
  - reports/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 93
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/93
```

## Background

After traces are persisted, the frontend still needs stable attempt-level queries and safe access to originals. Raw sessions may contain credentials, hidden tests, full thinking, and tool output, so public object keys or permanent URLs must not bypass platform authorization.

## Goals

- Provide APIs for a case attempt trace overview, session tree, paginated session spans, span details, and on-demand large content, including ingestion status, parser version, and data-quality metadata.
- Validate tenant, case-run, and attempt ownership for every query, using stable ordering and cursor pagination to bound deep-trace and cross-session result sizes.
- Issue time-limited downloads only for registered trace_file originals; require administrator permission and an audit record, and hide full thinking from read-only roles by default.
- Represent degraded, failed, unlinked, and non-comparable timing data explicitly, without leaking object credentials, sensitive content, or other tenants' information in errors.

## Acceptance

- A user can query the complete session tree for a selected attempt and page through spans, and switching attempts never reuses identifiers or content from another attempt.
- Cross-tenant, cross-project, and unauthorized requests are denied, arbitrary client-provided object keys cannot download unregistered objects, and expired URLs stop working.
- Raw downloads enforce permission and auditing, read-only roles receive policy-filtered thinking and sensitive fields, and APIs never return long-lived credentials.
- Backend tests cover pagination boundaries, deep-link ownership, authorization, auditing, degraded responses, and large-content reads, with no N+1 amplification in normal queries.

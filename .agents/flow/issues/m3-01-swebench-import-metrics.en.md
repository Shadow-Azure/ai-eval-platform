# Import SWE-bench slice and register metrics

English | [中文](m3-01-swebench-import-metrics.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: done
scope:
  - .agents/flow/issues/m3-01-swebench-import-metrics.*
  - backend/**
  - tests/**
adr: []
github:
  number: 45
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/45
```

## Background

M3 requires a real case to complete Agent execution, scoring, and result query. The prerequisite is an importable, validatable, browsable SWE-bench slice plus versioned metric semantics inside the platform. Dataset/version separation, adapter registration, and the immutable task view correspond to design requirements 2 and 2.1. Metric definitions must be versioned registry entries with platform-whitelisted aggregators only (decision D10), and the SWE-bench primary metric uses the official `% Resolved = resolved / total` semantics.

## Goals

- Implement the SWE-bench dataset adapter following the standard mapping in design doc 07 §1.1.
- Provide the backend import and validation flow for dataset versions; the task view is immutable and re-importing the same version is idempotent.
- Provide query APIs for dataset index, version detail, case detail, and metric definitions with server-side pagination.
- Register the official SWE-bench metric and generic versioned metric configurations; aggregators come from the platform whitelist.

## Acceptance

- An SWE-bench Verified slice can be imported and passes validation with a case count consistent with the version.
- Metric definitions are queryable as versioned registry entries with visible semantics; aggregators outside the whitelist never execute.
- Re-importing the same version returns an idempotent result without creating a second version or dirty tasks.
- Import validation failures (missing fields, broken patch) have explicit failure classes and diagnosable errors; pytest covers success and failure paths.

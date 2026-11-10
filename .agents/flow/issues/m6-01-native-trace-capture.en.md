# Complete native trace capture and multi-file manifest contracts

English | [中文](m6-01-native-trace-capture.md)

```yaml flow
kind: issue
milestone: m6
priority: P0
status: ready
scope:
  - .agents/flow/issues/m6-01-native-trace-capture.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - runner/**
  - runner-contract/**
  - backend/**
  - web/**
  - tests/**
  - plans/**
  - reports/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 90
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/90
```

## Background

M3 added native JSONL discovery to the runtime adapter, but the real task currently uploads only `trace/claude-stream.jsonl`, and the completion callback carries only one `trace_manifest`. Native root-agent sessions, subagent files, and `.meta.json` are not fully archived, so native evidence cannot later recover parent-child relationships.

## Goals

- Collect native session files and relationship metadata for root agents and subagents, preserve stdout/stderr as separate diagnostic artifacts, and retain produced traces when execution fails.
- Extend upload and completion contracts compatibly with attempt-bound multi-file manifests that record safe logical paths, digests, sizes, content types, runtime versions, and native format versions.
- Keep object keys platform-generated so runners cannot select arbitrary prefixes; keep legacy single-file manifests readable and mark missing native files as incomplete capture.
- Add runner contract and capture tests for root-only runs, parent-child runs, failed execution, and retried attempts to provide stable input for asynchronous normalization.

## Acceptance

- A real Claude Code run with subagents uploads all native sessions and relationship metadata, every manifest entry resolves to its case attempt, and host absolute paths are never exposed.
- Repeated reporting of the same file is unambiguous, retries create independent attempt manifests, and earlier attempt objects and manifests remain unchanged.
- Legacy `trace_manifest` data remains processable; stdout-only or partially captured native input is marked incomplete rather than presented as a complete trace.
- Runner, backend, and schema tests cover multiple files, duplicate digests, invalid paths, failure retention, and old-new contract compatibility.

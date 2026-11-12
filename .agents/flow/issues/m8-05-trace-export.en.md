# Trace export: download main-agent and subagent traces as a ZIP

English | [中文](m8-05-trace-export.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-05-trace-export.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - backend/app/api/traces.py
  - backend/app/services/trace_export.py
  - backend/app/services/trace_queries.py
  - tests/backend/test_trace_export_api.py
  - tests/trace-export-model.test.mjs
  - web/src/api/traces.ts
  - web/src/features/traces/traceExportModel.ts
  - web/src/pages/TracePage.tsx
  - web/src/styles/trace-viewer.css
  - prototype/**
  - plans/**
adr: []
github:
  number: 186
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/186
```

## Background

The trace page already shows the main agent and all subagents for an attempt, but raw traces can only be downloaded one file at a time: the header button actually takes `files[0]`, Raw source needs a click per file, and no manifest says whether every subagent is covered. The attempt needs one complete trace package.

## Goals

- Attempt-scoped export: package the current attempt's main-agent and all-subagent raw files into one ZIP.
- Archive under `main/` and `subagents/<agent>/` with a `manifest.json` recording case, attempt, sessions, file sha256 and missing entries.
- Add a "Download trace package" entry and a scope/directory/permission dialog on the trace page; admin-only and audited.
- When a native object is missing, skip it and mark it in the manifest instead of producing a broken archive or blocking the rest.

## Acceptance

- An admin gets a ZIP in one click with the main agent, all subagents and the manifest, consistent with the trace page counts.
- Read-only accounts receive 403 and anonymous requests 401; export writes an audit record and leaks neither object keys nor secrets.
- A missing object still yields an export with a manifest marked missing; an attempt with no exportable files returns a readable error.
- Backend tests, `npm test`, and `web` typecheck/build pass; the PR includes evidence for the entry point and ZIP contents.

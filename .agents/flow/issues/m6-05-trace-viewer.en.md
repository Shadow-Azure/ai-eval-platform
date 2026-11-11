# Implement the attempt-level three-pane trace viewer

English | [中文](m6-05-trace-viewer.md)

```yaml flow
kind: issue
milestone: m6
priority: P0
status: done
scope:
  - .agents/flow/issues/m6-05-trace-viewer.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - web/**
  - tests/**
  - docs/**
  - plans/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 94
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/94
```

## Background

The M5 case detail provides test and result entry points but no dedicated trace page. M6 needs a deep-linkable diagnostic view connecting a selected attempt's session tree, waterfall, and span attributes while preserving raw-file inspection when parsing degrades.

## Goals

- Add the prototyped trace route and case entry point, persist run, case, attempt, session, and span selection in the URL, and restore the same location after refresh, new-window navigation, or back navigation.
- Implement coordinated session-tree, waterfall, and span summary/content/attributes/raw-record panes with visible unlinked and degraded groups.
- Cancel or ignore stale requests and reset selections when changing attempts or sessions, build waterfalls only from normalized parent evidence, and fall back to ordinal layout with a warning for invalid timing.
- Load large text on demand and render it as safe text, covering loading, empty, error, processing, degraded, failed, and unauthorized states while following server permissions for raw access.

## Acceptance

- A user entering a selected attempt from a case row can choose a session and span, then copy the URL or refresh to restore the same view without losing context on browser back navigation.
- Rapid attempt switching never mixes in old sessions or spans, tree and waterfall relationships agree, and missing parents, unlinked groups, and non-comparable clocks have explicit indicators.
- Parsing failure does not hide test results, users see a redacted reason and can open raw files when authorized, and malicious HTML/Markdown or tool output never executes scripts.
- Frontend tests and visual comparisons cover pane coordination, deep links, long content, narrow windows, loading/empty/error/degraded states, and permission differences.

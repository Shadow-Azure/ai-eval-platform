# Redesign the trace viewer: conversation and turn timeline

English | [中文](m8-04-trace-viewer-redesign.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-04-trace-viewer-redesign.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/pages/TracePage.tsx
  - web/src/styles/trace-viewer.css
  - plans/**
  - prototype/**
adr: []
github:
  number: 182
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/182
```

## Background

The trace page already has a three-pane shell (session tree, waterfall, span attributes), but reading a real trace is expensive: 76 flat spans, 75 with identical start/end showing 0ms, duplicate rows for every tool call, and no conversation layer or turn navigation.

## Goals

- Add a conversation view and a turn timeline as the default reading entry, while keeping raw span / attributes / raw-file entries.
- Derive turns in the frontend from LLM calls and tool boundaries; no new backend table or field, and no fabricated duration for zero-width spans.
- Finish an interactive prototype and before/after screenshots first, then enter frontend development after the direction is confirmed.

## Acceptance

- Opening the trace page shows conversation turns by default, expandable/collapsible, with the right pane following the selected turn.
- The timeline expresses turn windows and where time went; zero-width spans are explicitly labelled rather than drawn as misleading 0ms bars.
- Existing session tree, kind filters, deep links, raw files and full span content entries stay; `web` typecheck and build pass.

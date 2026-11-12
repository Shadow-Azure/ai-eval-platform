# m8-04 Redesign the session trace viewing experience

English | [中文](m8-04-trace-view-redesign.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-04-trace-view-redesign.*
  - .agents/flow/issues/m8-03-eval-records-pagination.*
  - .agents/flow/issues/m8-06-agent-asset-not-injected.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/**
  - plans/**
adr: []
github:
  number: 182
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/182
```

## Background

The evaluation / session trace page currently flattens 76 spans into three panes: session tree, waterfall, and attribute table. In a real trace, 75 / 76 spans share the same start and end instant and `duration_ms` is empty, so the waterfall degenerates into zero-width ticks. Each tool call also produces duplicate message / tool_result rows, forcing the reader to reconstruct `LLM → thinking → Bash → tool_result` mentally. M8-04 redesigns the viewing experience on the existing three-pane shell without expanding the backend data model.

## Goals

- Default to a conversation view: merge spans into phases, collapse turns, and show each step's tool, target, and status.
- Provide a step view: every step starts collapsed and expands by type—Bash command/output, Edit / Write diff, Read path/content, Skill / Subagent nested steps.
- Provide a phase timeline that shows where time went; label zero-width spans explicitly instead of fabricating durations.
- Keep a readable inspector with `overview / input / output / attributes / raw` drill-down; retain raw files, full span content, and signed downloads.
- Preserve existing attempt / session / span / kind deep links, quality banners, loading, and error states.

## Acceptance

- The trace page defaults to a conversation view with collapsible phase cards; selecting a step updates the inspector.
- The step view starts fully collapsed; Bash, Edit / Write, Read, and Skill / Subagent expand to command/output, diff, path/content, and nested steps respectively.
- The phase timeline shows where time went; zero-width spans are labelled explicitly and never rendered as misleading 0ms bars.
- At least one of search, type filter, or error focus works; existing deep links and filtering behavior are preserved.
- `web` typecheck / build pass; the PR includes before/after screenshots and a smoke report.

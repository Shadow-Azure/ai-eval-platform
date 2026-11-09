# Implement Claude Code trace normalization and parent-child recovery

English | [中文](m6-03-claude-code-trace-normalizer.md)

```yaml flow
kind: issue
milestone: m6
priority: P0
status: ready
scope:
  - .agents/flow/issues/m6-03-claude-code-trace-normalizer.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - backend/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 92
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/92
```

## Background

The unified model becomes useful for diagnosis only after a real runtime parser is connected. Claude Code parent-child evidence spans Agent tool calls, `.meta.json`, agent IDs, and sidechain files, while `parentUuid` describes only the message tree; guessing from time or sessionId would misattach parallel agents, forks, or resumes.

## Goals

- Implement an explicitly versioned parser for Claude Code native files, treating final native files as authoritative instead of parsing only headless stdout.
- Recover parent-child sessions from Agent tool_use, `.meta.json` toolUseId, toolUseResult/agentId, and child-file evidence; place unproven entries in unlinked or degraded groups.
- Normalize AGENT, LLM, TOOL, MESSAGE, and THINKING spans with hierarchy, ordering, timing, status, standard attributes, content previews, and object references, using final token and cost results rather than summing deltas.
- Build redacted golden fixtures pinned to a runtime version for root-only runs, multiple subagents, fork/resume, duplicate deltas, missing child files, unknown versions, and large tool output.

## Acceptance

- Parent-child relationships require native evidence, `parentUuid` is used only for message/span hierarchy, equal sessionIds and temporal adjacency never establish session ancestry.
- Parallel subagent ordering, ownership, and final usage are accurate, duplicate messages are not persisted twice, and spans with missing parents remain identifiable root fragments.
- Missing child files or unknown formats retain browsable fragments and raw access without speculative parsing; large bodies are stored only as truncated previews and controlled references.
- Golden fixtures and backend tests cover these scenarios and verify attribute naming, error summaries, redaction, and that malicious text never becomes executable content.

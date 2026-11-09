# Claude Code runtime adapter and execution image

English | [中文](m3-03-claude-code-runtime-adapter.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m3-03-claude-code-runtime-adapter.*
  - backend/**
  - runner/**
  - infra/**
  - tests/**
adr: []
github:
  number: 47
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/47
```

## Background

M3 specifies the Claude Code runtime. Design doc 07 §4 defines the RuntimeAdapter protocol and asset mapping (Skill/Command/Subagent to `.claude/`). Launch command templates are platform-reviewed code and never accept arbitrary shell injection from the UI; parameters bind through structured variables. Traces treat the final native files as authoritative (decision D8); parsing and parent-child relations belong to M6, so M3 only collects and stores them. The Runner uses an isolated runtime HOME with an allowlist to control asset loading surfaces — simply adding `--bare` is not acceptable.

## Goals

- Implement the minimal `RuntimeAdapter` protocol: `validate_assets`, `materialize_assets`, `command`, `consume_stream` (minimal LiveEvent), and `discover_trace_files`.
- Implement asset snapshot assembly: isolated runtime HOME, loading Skills/Commands/Subagents through an allowlist, with tests proving only snapshot assets are loaded.
- Build the evaluation execution container image: bundling Claude Code, task dependencies, and scoring prerequisites, with a queryable digest.
- Implement the pre-launch consistency check (07 §5): expired credentials, unresolved refs, missing images, and capability mismatches must block; insufficient capacity only queues with a warning.

## Acceptance

- Adapter tests cover asset mapping and command generation: structured variable binding with no shell-injection surface in generated commands.
- The execution image builds reproducibly and its digest is recorded into the task manifest.
- Consistency checks grade blockers versus warnings correctly, with pytest covering every blocking path.
- A mock task completes a Claude Code session inside the execution image and produces raw trace files in the Runner workspace (collection only; parsing belongs to M6).

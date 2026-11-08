# Add the Claude Code runtime and single-case execution bundle

English | [中文](m3-03-claude-code-runtime.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
  - runner/**
  - runner-contract/**
  - .github/workflows/**
adr: []
github:
  number: 25
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/25
```

## Background

Runners can execute deterministic synthetic tasks but cannot drive a real agent yet. M3 needs a registered runtime adapter that prepares code and assets in an isolated sandbox, runs Claude Code, and collects patches and session files.

## Goals

- Register agent_runtime and the RuntimeAdapter interface, implementing Claude Code headless support with an isolated HOME, asset mapping and stream-json parsing.
- Extend the in-job runner to claim task bundles, prepare the repository and environment, run the agent, collect patches and session files, and upload them to object storage.
- Separate agent execution from scoring, keeping artifact manifests and callback schemas versioned and consistent.
- Distinguish environment, configuration, agent and upload failures, and classify each as retryable or fail-fast.

## Acceptance

- A single SWE-bench case completes agent execution in a Linux sandbox and produces a patch and native session files.
- The asset snapshot loads only the selected skills, commands and subagents, and the agent cannot read scoring-only files.
- Non-zero agent exit, timeout and upload failure all report diagnosable errors instead of a successful terminal state.
- Local fixtures and the real workflow share the same manifest and callback schemas.

# Build model, credential and Git asset pages

English | [中文](m5-02-model-asset-pages.md)

```yaml flow
kind: issue
milestone: m5
priority: P1
status: backlog
scope:
  - .agents/flow/**
  - web/**
  - backend/**
  - tests/**
  - package.json
  - package-lock.json
  - docs/**
  - plans/**
  - reports/**
adr: []
github:
  number: 34
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/34
```

## Background

Model keys, temporary credentials and Git assets are required inputs for launching an evaluation; users need to configure and validate them safely in the UI and see immutable snapshots.

## Goals

- Build model configuration list, create, edit and connectivity test flows where keys are write-only and support rotation and revocation.
- Add agent_asset_source, snapshot and asset tables and APIs covering Git sources, ref resolution, scanning and file trees.
- Build asset pages that show skills, commands, subagents and runtime compatibility hints.
- Ensure temporary keys never reach the URL, local storage, logs or query cache.

## Acceptance

- Saved keys are never echoed, rotation invalidates the old credential, and historical evaluations still read the mask.
- Refs resolve to immutable commits, scan results are reproducible and support tree selection.
- Unsupported or degraded asset types produce explicit hints instead of being silently ignored.
- Pages match the prototype, and key forms and error states have tests.

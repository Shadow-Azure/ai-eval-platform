# [m8-21] Full source mode: clone + scan/build by ref with default-branch and version selection

English | [中文](m8-21-source-mode-ref-selection.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: backlog
scope:
  - .agents/flow/issues/m8-21-source-mode-ref-selection.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - prototype/**
  - backend/app/**
  - backend/migrations/versions/**
  - runner/**
  - web/**
  - tests/**
  - docs/**
  - plans/**
adr: []
github:
  number: null
  url: null
```

## Background

- Evaluations currently use the load-test frozen asset snapshot plus a prebuilt tarball: fast and stable, but switching branch/tag/commit requires repackaging and the version cannot be changed per evaluation.
- The command-less SuperPower source (`obra/superpowers`) stores assets in native directories (`skills/`, `.agents/`, `.claude-plugin/`, `.opencode/`, `.pi/`, …); the platform loader scans and normalizes them into `.claude` — the repo has no `.claude`.

## Goals

- Full source mode: every evaluation clones the asset repo at the chosen ref; command-less assets are scanned/normalized by the platform, CLI-generated assets run install/init to produce `.claude`; the frozen snapshot is only a load-test validation record.
- The asset source keeps a default branch; the eval preparation page accepts an optional agent version (branch/tag/commit); the backend resolves it to a commit and sends it in the payload; the trace records repo/ref/commit.

## Plan

### Two asset classes
| Class | Repo content | At evaluation |
| --- | --- | --- |
| command-less (SuperPower) | `skills/`, `.agents/`, `.opencode/`, `.pi/` | clone+checkout → platform scan/normalize → `.claude` → inject; no install/init |
| CLI-generated (CodeSpec) | source + install/init | clone+checkout → install/build → init generates `.claude` → inject |

### Flow
prepare ref → backend resolves commit → payload → runner clone/checkout → scan or install/init → inject → agent.

### Frontend prototype (before / after)

Before (asset source shows "default Ref", evaluation shows "evaluation Ref"): the two screenshots below.

After (asset source shows "default branch", evaluation shows "Agent version (branch / tag / commit)" with copy explaining clone + scan/build): the two screenshots below.

![Before - asset source](https://raw.githubusercontent.com/Shadow-Azure/ai-eval-platform/agent/m8-21/superpower-agent-eval-41d71917e720/prototype/screenshots/m8-21-before-assets.png)
![Before - evaluation agent version](https://raw.githubusercontent.com/Shadow-Azure/ai-eval-platform/agent/m8-21/superpower-agent-eval-41d71917e720/prototype/screenshots/m8-21-before-eval.png)
![After - asset source](https://raw.githubusercontent.com/Shadow-Azure/ai-eval-platform/agent/m8-21/superpower-agent-eval-41d71917e720/prototype/screenshots/m8-21-after-assets.png)
![After - evaluation agent version](https://raw.githubusercontent.com/Shadow-Azure/ai-eval-platform/agent/m8-21/superpower-agent-eval-41d71917e720/prototype/screenshots/m8-21-after-eval.png)

## Phases

1. asset loader scans by ref (command-less);
2. prepare API + preparation page ref override;
3. runner full source mode (drop frozen-snapshot injection);
4. frontend/docs/tests and removal of the old injection path.

## Acceptance

- The same agent asset can switch branch/tag/commit at evaluation start and be cloned/scanned/built at that version.
- The trace records repo/ref/commit; both asset classes inject and run.
- Before/after prototype screenshots are above.

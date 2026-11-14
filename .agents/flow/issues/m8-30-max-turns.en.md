# [m8-30] Raise the default MAX_TURNS for complex command flows to 80

English | [中文](m8-30-max-turns.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-30-max-turns.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - runner/scripts/real-task.mjs
  - tests/runner/max-turns.test.mjs
adr: []
github:
  number: 244
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/244
```

## Background

OpenSpec apply requires a subagent. Retest `eec9ea5e` marked subagent in task.md but ended before dispatch. `real-task.mjs` defaults `MAX_TURNS=30`; design+propose exhausted it.

## Goal

- Default MAX_TURNS 30 -> 80; `REAL_TASK_MAX_TURNS` remains overridable.
- Lock with a runner test; rerun subagent apply after deploy.

## Acceptance

- Default 80; tests pass; trace shows Task tool/child session.

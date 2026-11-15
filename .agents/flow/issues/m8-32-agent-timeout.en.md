# [m8-32] Raise the Agent timeout to 30 minutes

English | [中文](m8-32-agent-timeout.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-32-agent-timeout.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - runner/scripts/real-task.mjs
  - tests/runner/agent-timeout.test.mjs
  - prototype/app.js
  - prototype/index.html
adr: []
github:
  number: 248
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/248
```

## Background

OpenSpec retest `ccbdb2f0` dispatched subagents but hit the 900s (15 min) wall-clock timeout; official tests never ran and the score was `agent_failed`.

## Goal

- `TIMEOUT_MS` default 1800000 (30 min).
- Outer `REAL_TASK_AGENT_TIMEOUT_MS` default 1900000, leaving cleanup margin.
- Env overrides remain; lock with a runner test.

## Acceptance

- 30-minute default; tests pass; post-deploy retest completes final verification.

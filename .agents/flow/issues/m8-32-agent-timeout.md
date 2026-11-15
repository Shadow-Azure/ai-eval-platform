# [m8-32] Agent 超时提高到 30 分钟

[English](m8-32-agent-timeout.en.md) | 中文

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
adr: []
github:
  number: 248
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/248
```

## 背景

OpenSpec 复测 `ccbdb2f0` 已跑通 subagent，但 Agent 在 900s（15 分钟）wall-clock 超时，official test 未执行，score 为 `agent_failed`。

## 目标

- `TIMEOUT_MS` 默认 1800000（30 分钟）。
- 外层 `REAL_TASK_AGENT_TIMEOUT_MS` 默认 1900000，留清理余量。
- 环境变量仍可覆盖；runner 测试锁定。

## 验收

- 默认 30 分钟；测试通过；部署后复测能完成 final verification。

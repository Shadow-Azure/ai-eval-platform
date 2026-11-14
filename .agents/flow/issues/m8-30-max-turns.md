# [m8-30] 提高复杂 command 流默认 MAX_TURNS 到 80

[English](m8-30-max-turns.en.md) | 中文

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

## 背景

OpenSpec apply 要求 subagent；复测 `eec9ea5e` 已在 task.md 标记 subagent，但 trace 在派发前结束。`real-task.mjs` 默认 `MAX_TURNS=30`，design+propose 已耗尽。

## 目标

- 默认 MAX_TURNS 30 → 80，`REAL_TASK_MAX_TURNS` 仍可覆盖。
- runner 测试锁定；部署后重跑 subagent apply。

## 验收

- 默认 80；测试通过；trace 出现 Task tool/子 session。

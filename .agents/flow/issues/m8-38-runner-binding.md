# [m8-38] 修复 instance→Runner→attempt 绑定，确保 spot 中断快速重试可触发

[English](m8-38-runner-binding.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: done
scope:
  - .agents/flow/issues/m8-38-runner-binding.*
  - .agents/flow/issues/m8-37-spot-retry-fallback.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/services/runners.py
  - runner/scripts/real-task.mjs
  - tests/backend/test_runners.py
  - tests/runner/heartbeat-integration.test.mjs
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
adr: []
github:
  number: 261
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/261
```

## 背景

m8-37 快速重试不触发：running attempt 的 `runner_id` 为空，Runner `busy_case_attempt_id` 被 online 心跳清空，`_reclaim` 无法映射 active attempt。

## 目标

- started 回调用 `RUNNER_ID || RUNNER_NAME`，让 `attempt.runner_id` 落库。
- online 心跳保留 busy 绑定；只有 terminal/offline 清理。
- 回归测试覆盖 runner_id 兜底与绑定保留。

## 验收

- started 后 attempt.runner_id 非空；online 心跳不清 busy；provider_stopped 能命中 attempt；CI 通过。

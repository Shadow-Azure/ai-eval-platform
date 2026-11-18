# [m8] 修正 execute job 超时与 Agent/用例预算不匹配

[English](m8-43-job-timeout-headroom.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-43-job-timeout-headroom.*
  - .github/workflows/eval-shard.yml
  - backend/app/agent_timeout.py
  - backend/app/config.py
  - backend/app/services/case_attempts.py
  - backend/app/services/dispatcher.py
  - backend/app/services/evaluations.py
  - backend/app/services/lifecycle.py
  - backend/app/services/preparations.py
  - docs/architecture.*
  - runner-contract/schemas/v1/dispatch.schema.json
  - runner-contract/schemas/v1/failure.schema.json
  - runner/lib/agent-exec.mjs
  - runner/lib/cancellation.mjs
  - runner/scripts/real-task.mjs
  - runner/workflows/eval-shard.yml
  - scripts/doc-budgets.manifest.json
  - tests/backend/test_execution_timeout_hierarchy.py
  - tests/backend/test_failure_classification.py
  - tests/backend/test_preparations.py
  - tests/eval-shard-workflow.test.mjs
  - tests/runner/agent-exec.test.mjs
  - tests/runner/agent-timeout.test.mjs
  - tests/runner/cancellation.test.mjs
  - tests/runner/contracts.test.mjs
adr: []
github:
  number: 304
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/304
```

## 背景

一次评测冻结 `agent_timeout_seconds=3600`、`case_timeout_seconds=4200`，但 `execute` job 写死 `timeout-minutes: 60`。job 比 Agent 先启动，60 分钟到点就被 GitHub 终止，Runner 没有机会上传 trace、评分或发终态回调；平台 300 秒后按 `heartbeat_timeout` 收口并自动重试，同一用例两次 attempt 仍无产出。

## 目标

- 建立并校验 `dispatch TTL > job timeout > case timeout > agent timeout` 层级；相等或更短的 case 自动抬高，无法留出余量的 Agent 预算拒绝。
- `execute` job 从 dispatch payload 读取派生的 `job_timeout_minutes`，不再使用固定 60 分钟。
- Runner 在 SIGTERM/SIGINT 时停止 Agent、上传 partial trace 并发送终态 `runner_lost` 失败回调；该失败不再触发全量重试。
- 平台把成功上传的 partial trace 入队解析；没有任何 trace 的终态失败明确记为 `unlinked`。
- 取消宽限期缩短到 60 秒，避免 runner 已回调后仍等待满 10 分钟。

## 验收

- 60 分钟 Agent 的评测中 job 超时严格大于 case、case 严格大于 Agent；改 Agent 预算后 job 超时随之变化。
- Runner 被取消时平台收到 `runner_lost`/`execution_timeout` 终态回调，不自动重跑；若已上传 trace，轨迹状态为 `incomplete`，否则为 `unlinked`。
- 超时层级、取消回调与重试分类均有单测；`npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与 runner 契约测试通过。
- 文档记录超时层级、默认余量、动态 job 超时与 60 秒取消宽限期。

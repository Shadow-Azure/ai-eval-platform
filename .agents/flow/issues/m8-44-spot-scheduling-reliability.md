# [m8-44] 竞争态 spot 池调度可靠性：重试回调、失败结算、runner 退出分类与容量核算

[English](m8-44-spot-scheduling-reliability.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-44-spot-scheduling-reliability.*
  - .github/workflows/eval-shard.yml
  - backend/app/services/case_attempts.py
  - backend/app/services/capacity.py
  - backend/app/services/lifecycle.py
  - backend/app/services/provisioning/aliyun.py
  - backend/app/services/workbench.py
  - docs/architecture.*
  - plans/m1-02-fullstack-design/**
  - plans/2026-11-19-pr*-review.md
  - reports/**
  - runner-contract/schemas/v1/failure.schema.json
  - scripts/doc-budgets.manifest.json
  - tests/backend/test_aliyun_provisioner.py
  - tests/backend/test_case_callbacks.py
  - tests/backend/test_capacity_scheduling.py
  - tests/backend/test_eval_workbench.py
  - tests/backend/test_failure_classification.py
  - tests/backend/test_spot_interruption.py
  - tests/eval-shard-workflow.test.mjs
adr: []
github:
  number: 306
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/306
```

## 背景

一次 m9 生产 spot×SuperPower 评测暴露了四类与模型 429 无关的调度可靠性缺陷：手动重试把平台事件写到新 attempt 的 sequence 0，与 Runner 的 `case.started` 撞唯一键导致 started 500；execute job 在 `started` 之前失败时平台没有回调，只能等满 3600 秒 queue timeout；`runner_exited`/`runner_heartbeat_lost` 被当成 `provider_stopped`，错误文案固定且消耗 spot 重试预算；两条评测共享一个池时各自按全池容量计算，busy runner 被重复当成可用容量。spot 重试预算还与 `max_attempts` 脱节，出现过 attempt 4。

## 目标

- 手动重试的 `case.retry_requested` 挂到旧 attempt 并取空闲序号；`started` 遇到序号冲突时回读事件、幂等返回或返回可诊断 409，不再 500。
- execute job 在 started 前失败（脚本包、checkout、dispatch 校验、执行失败）时，用 manifest 端点的短期 token 回调 `runner_bootstrap_failed`；平台按普通重试预算立即结算。
- 只有 provider 确认停止的回收走 `spot_interrupted`；`runner_exited`/`runner_heartbeat_lost` 走 `runner_lost`，保留真实 reason、provider_state 与 last_heartbeat，不消耗 spot 预算。
- 池级 in-flight 预留：第二个评测只看到第一个评测未占用的容量；started 时再按池上限做硬闸，保证并发 Agent 数不超过 `max_capacity`。
- spot 重试同时受 `SPOT_RETRY_LIMIT` 和 run 的 `max_attempts` 约束，`attempt_no` 不超过声明上限。

## 验收

- 手动重试后 attempt 2 `started` 返回 200 并能完成；存在历史 `retry_requested` 序号冲突时 started 自动迁移平台事件并成功。
- `runner_bootstrap_failed` 在 started 前结算为可重试失败并生成 attempt 2，不等待 queue timeout。
- Running 实例上的 `runner_exited` 记录 `runner_lost` 与真实 reason，按 `max_attempts` 重试；不产生 `case.spot_interrupted`，不增加 spot 重试计数。
- 两条评测共享池：第一条 5 个 attempt 在飞时第二条 effective=0 且不释放分片；释放一个槽位后第二条只释放一个分片。
- spot 重试在 `max_attempts=2` 时最多生成 attempt 2。
- `pytest tests/backend`、`npm test`、`npm run verify:flow` 与 runner 契约测试通过。
- 本次不包含 #307 的分片进度租约、spot→fixed 回退、warm pool、脚本包预置与 infra 上报；这些保留在 issue #306 的后续阶段。

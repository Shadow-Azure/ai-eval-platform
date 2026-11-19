# [m8-46] spot 连续回收后回退 fixed/按量资源

[English](m8-46-spot-fallback-fixed.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/**
  - backend/**
  - docs/**
  - plans/**
  - tests/**
  - web/**
adr: []
github:
  number: 311
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/311
```

## 背景

#306 现场中 13236 的 4 次 attempt 全部因 spot 回收失败，最终 `spot_retry_exhausted`；窗口内 31 台 spot、29 次中断说明 spot 可用性会长时间波动。现有重试只会继续向同一个竞价池申请实例，预算耗尽后没有任何回退路径。

## 目标

- 同一 run 连续 N 次 provider 侧 spot 回收，或容量控制器连续 N 次因库存不足/价格高于阈值无法扩容后，切换到已有的固定/按量资源池。
- N、兜底池和可选成本上限可配置；不新增云厂商，不自动创建新池。
- 回退事件写入 run 配置快照与资源事件，评测详情显示“已回退”，不再直接以 `spot_retry_exhausted` 收口。
- 未配置兜底池时保持原有 spot 重试行为。

## 方案

- 在 spot 池 `instance_config` 中新增 `fallback_pool_id`、`fallback_after`、`fallback_max_price_cny`；兜底池必须是同租户的 active 固定/按量池。
- `spot_fallback` 服务维护 run 级连续失败/连续供给阻塞计数；成功用例重置计数。达到阈值后把 `eval_run.resource_pool_id` 切到兜底池，并改写尚未 dispatch 的 outbox/manifest 的 `runs_on` 与待派发 attempt 的池引用。
- 已回退的 run 不再消耗 spot 重试预算，只受 `max_attempts` 约束；retry 在回退切换之后构建，因此 manifest、attempt 与 dispatch 都指向兜底池。
- run 快照写 `spot_fallback` 状态；新增 run 级 `eval.spot_fallback` 与池级 `resource.spot.fallback` / `resource.spot.fallback_suppressed` 事件。
- 启用成本上限时，兜底池必须声明 `reference_price_cny`（或已有价格）；成本未知或超上限时不回退，记录抑制原因并继续既有重试。
- 评测详情「执行组合」新增资源回退行；资源池创建/编辑支持选择兜底池、阈值与上限，详情抽屉展示配置，资源事件列表识别新事件类型。

## 验收

- 强制连续 3 次 provider 侧 spot 回收，用例自动切到固定/按量池并继续完成，run 显示“已回退”，不出现 `spot_retry_exhausted`。
- 连续库存不足或价格高于阈值达到阈值后，同样切换 run 池；已经发送到旧 spot 池的 Job 不丢失，后续 retry 使用新池。
- 兜底池成本高于上限或成本未知时，回退被抑制，资源事件记录稳定原因，原有 spot 重试继续。
- 成功用例将连续失败计数归零；未配置兜底池时行为与修改前一致。
- `pytest tests/backend`、`npm test`、`npm run verify:flow`、`npm run verify:docs` 与 web 构建通过。

## 范围

- 涉及：`backend/app/config.py`、`backend/app/services/spot_fallback.py`、`backend/app/services/case_attempts.py`、`backend/app/services/capacity_control.py`、`backend/app/services/workbench.py`、`backend/app/api/resources.py`、`web/src/api/`、`web/src/pages/`、`tests/backend/`、`tests/integration/`、`docs/`、`plans/`。
- 不涉及：新云厂商、自动创建固定池、市场竞价策略、模型供应商 429、#306 已完成的失败分类与容量核算。

# 实现模型预算暂停与恢复

[English](m4-05-budget-pause-resume.en.md) | 中文

```yaml flow
kind: issue
milestone: m4
priority: P0
status: done
scope:
  - .agents/flow/issues/m4-05-budget-pause-resume.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - web/**
  - tests/**
  - reports/**
  - plans/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 82
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/82
```

## 背景

模型网关已经按评测累计费用并在达到预算后拒绝新请求，但评测状态机没有进入暂停，调度器仍可派发新用例，用户也无法增加预算后继续。并发评测上线后，单纯在网关注入 402 会让未完成任务混杂失败和排队原因。

## 目标

- 为评测维护预算版本和权威已花费金额，模型网关达到预算时触发平台级暂停，而不是只返回单次请求错误。
- 实现 `pausing_budget` 到 `paused_budget` 流程：停止新派发和新模型调用，等待活跃 attempt 上传已有产物并安全退出。
- 提供增加预算的恢复 API，原子更新预算与版本、写审计，并只重新调度未完成用例。
- 暂停期间不生成最终指标、不自动切换模型或资源，已完成用例的结果与成本保持不变。

## 验收

- 达到预算后不再产生新的模型调用或新分片派发，活跃用例可以完成上传并记录 `pause_reason=budget_exceeded`。
- 所有活跃 attempt 收口后评测进入 paused_budget，页面与 API 能展示预算、已花费、已完成和未完成数量。
- 并发或重复恢复请求保持幂等，budget_version 不匹配时拒绝，恢复后只创建未完成用例的新 attempt。
- 暂停、恢复、越界、并发确认和网关拒绝路径均有自动化测试，最终指标只在评测真正结束或恢复运行后生成。

# 完善评测详情与结果工作台

[English](m5-05-eval-results-workbench.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P0
status: done
scope:
  - .agents/flow/issues/m5-05-eval-results-workbench.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
  - plans/**
adr: []
github:
  number: 87
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/87
```

## 背景

现有评测详情页已展示状态、结果卡、用例表、测试明细抽屉和事件时间线，但缺少结构化分区、筛选搜索、配置快照、失败重试、预算恢复和大日志查看。随着 M4 引入并发、超时、取消和预算暂停，详情页需要成为解释执行结果和采取恢复操作的统一工作台。

## 目标

- 重组评测详情为概览、用例、测试与结果、配置快照、事件日志等清晰分区，并保持 URL 状态和深链恢复。
- 支持按执行状态、结果状态、轨迹状态搜索筛选用例，查看 attempt 历史、失败摘要、测试日志和对象链接。
- 增加取消、失败用例重试、预算暂停展示与恢复入口，并让动作后的状态通过 SSE 或刷新保持一致。
- 展示指标口径、分母、是否最终、并发、超时、重试、成本和资源池等运行快照，未知指标类型安全降级。

## 验收

- 运行中刷新或断线重连不丢进展、不回退状态，终态停止无意义重连，旧评测事件不会混入当前详情。
- 用户可以筛选并打开单个用例看到最新及历史 attempt、测试级结果和可访问的失败日志。
- 取消、重试和预算恢复操作有权限、确认、错误和并发状态处理，操作结果能在详情和事件日志中追溯。
- 关键交互、状态分区、空态、错误态、权限和指标降级均有测试或视觉对照证据。

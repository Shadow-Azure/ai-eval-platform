# 实现评测详情、实时进展与结果对比

[English](m5-05-eval-detail-results.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P0
status: backlog
scope:
  - .agents/flow/**
  - web/**
  - backend/**
  - tests/**
  - package.json
  - package-lock.json
  - docs/**
  - plans/**
  - reports/**
adr: []
github:
  number: 37
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/37
```

## 背景

评测创建后，用户需要看到实时进展、用例状态、测试明细、结果口径和基线对比，而不是等结束后只看一个数字。

## 目标

- 实现详情页状态头、堆叠进度、指标卡、预算暂停条以及取消和重试操作。
- 实现用例表、测试明细、结果、配置快照和事件时间线五个页签。
- 实现基线自动推荐与手动选择对比，展示推荐理由和配置差异。
- 接入 SSE 增量刷新与断线恢复，运行中刷新不丢进度。

## 验收

- 运行中刷新或重连不丢进展，终态停止无意义重连。
- 指标卡展示口径、分母和 is_final，预算暂停状态有恢复入口。
- 用例可按状态、结果和轨迹状态筛选，并查看测试级结果与失败日志。
- 对比展示期望的指标差值与配置差异，未知渲染器安全降级。

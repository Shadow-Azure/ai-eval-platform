# 实现资源连接与资源池页面

[English](m5-03-resource-pages.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P1
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
  number: 35
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/35
```

## 背景

资源连接、竞价价格、容量和 Runner 心跳需要在页面上可见，否则用户无法判断评测为什么排队或无法供给。

## 目标

- 实现连接与资源池 API 和页面，展示供给模式、价格阈值、容量和在线 Runner。
- 实现有费用提示和二次确认的供给测试，并记录审计。
- 校验连接能力与池类型，不匹配时拒绝创建并说明原因。
- 展示 Runner 标签、最近心跳、当前任务和离线原因。

## 验收

- 连接能力不匹配时创建池被拒绝，页面给出明确原因。
- 池页展示当前价格、阈值、在线 Runner、最近心跳和下次检查时间。
- 供给测试需要二次确认且写审计，不会静默产生费用。
- 空态、错误态和无权限状态完整。

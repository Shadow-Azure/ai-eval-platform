# 提供评测事件流与 SSE 断线回放

[English](m4-05-sse-event-stream.en.md) | 中文

```yaml flow
kind: issue
milestone: m4
priority: P1
status: backlog
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
adr: []
github:
  number: 32
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/32
```

## 背景

页面需要低延迟看到排队、执行、评分和容量变化，同时刷新或断线后进度不能丢失或回退。SSE 只做通知，数据库事件游标才是恢复依据。

## 目标

- 提供历史事件分页与 SSE 端点，支持 Last-Event-ID 回放和游标过旧的 resync_required。
- 定义 run、case、metric、capacity 和 notice 事件类型与脱敏 DTO，只携带变更摘要。
- Redis 只负责唤醒，事件正文从数据库按游标读取，Redis 重启不丢事实。
- 终态发送最终事件后停止重连，并按 case、attempt、sequence 单调应用。

## 验收

- 刷新和重连不丢事件，进度不回退，旧 run 事件不会混入。
- 重复和乱序事件被忽略，游标过旧时返回 resync_required。
- SSE 不暴露内部 outbox 或未脱敏字段。
- 断线重连、Redis 重启和终态停止重连有自动化测试。

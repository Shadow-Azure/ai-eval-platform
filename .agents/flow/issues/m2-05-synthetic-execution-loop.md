# 打通合成评测的派发、执行、回传与状态持久化

[English](m2-05-synthetic-execution-loop.en.md) | 中文

```yaml flow
kind: issue
milestone: m2
priority: P0
status: done
scope:
  - .agents/flow/issues/m2-05-synthetic-execution-loop.*
  - .agents/flow/milestones/m2-executable-foundation.*
  - backend/**
  - web/**
  - runner/**
  - runner-contract/**
  - tests/**
  - .github/workflows/**
  - deploy/docker-compose.prod.yml
  - reports/**
  - plans/**
adr: []
github:
  number: 15
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/15
```

## 背景

M2 的最终证据不是单独组件通过，而是一个合成评测从平台创建开始，经 GitHub 派发到固定 Runner，再通过 OSS 与回调把状态持久化回平台。只有这条链路真实成立，M3 接入模型和数据集才值得开始。

## 目标

- 提供最小合成评测创建、查询、事件读取和取消接口。
- 使用事务 outbox 生成分片派发，调用 repository_dispatch 并保存对账信息。
- 接收 started、heartbeat、completed 与 failed 回调，校验 attempt 和幂等键后更新状态。
- 提供最小 Web 或 API 视图展示合成评测从 queued 到终态的过程，并覆盖重启恢复。

## 验收

- 一次合成评测能通过真实 GitHub workflow 在固定 Linux Runner 上执行，并将结果对象与终态写回 PostgreSQL。
- 重复回调、乱序回调和 Worker 重启都不会产生重复 attempt 或回退状态。
- 故意制造派发失败、Runner 失败和对象缺失时，系统进入可诊断的失败状态而不是永久 queued。
- 在不调用任何模型的条件下重复跑通整条链路，并生成可供 M3 复用的契约与测试 fixture。

# 补齐执行超时、取消与对账恢复

[English](m4-02-execution-lifecycle-recovery.en.md) | 中文

```yaml flow
kind: issue
milestone: m4
priority: P0
status: ready
scope:
  - .agents/flow/issues/m4-02-execution-lifecycle-recovery.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - runner/**
  - runner-contract/**
  - .github/workflows/**
  - tests/**
  - docs/**
  - plans/**
  - reports/**
  - deploy/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 79
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/79
```

## 背景

M2 和 M3 已具备回调用例状态机、短期 token、可重试失败的新 attempt 和 outbox lease 恢复，但取消主要停留在数据库状态，执行超时、上传宽限期、GitHub Job 远程终止和跨系统对账仍不完整。Runner 失联或回调丢失时，用例可能长期停在 queued 或 running。

## 目标

- 定义 queued、Agent 执行、评分、上传宽限期和心跳五类超时，并为每一类生成稳定错误码与可诊断终态。
- 取消评测时停止新派发、撤销短期任务与模型 token，并请求 GitHub 取消对应 workflow 或 job；迟到回调只保留审计，不覆盖取消事实。
- 增加对账 worker，结合 GitHub run/job 状态、心跳和已上传对象补建丢失的完成事件或收口失联 attempt。
- 对失败进行分类，环境与传输故障可重试，配置错误快速失败，评分阶段失败避免无必要地重跑 Agent。

## 验收

- 五类超时均有边界测试，超时、心跳丢失和对象缺失不会让用例永久处于 queued 或 running。
- 取消后不再出现新派发或新模型调用，远程取消失败时平台仍保留 cancelling/cancelled 事实和可重试诊断。
- 旧 attempt 的迟到回调返回 stale 状态并保留审计，worker 重启、outbox 重放和回调丢失场景可以自动恢复。
- 重试保留历史 attempt、轨迹和成本，最新有效结果不会被旧回调覆盖。

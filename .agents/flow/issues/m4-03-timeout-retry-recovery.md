# 补齐超时、取消、重试与恢复对账

[English](m4-03-timeout-retry-recovery.en.md) | 中文

```yaml flow
kind: issue
milestone: m4
priority: P0
status: backlog
scope:
  - .agents/flow/**
  - backend/**
  - runner/**
  - runner-contract/**
  - tests/**
  - deploy/**
  - .github/workflows/**
  - docs/**
  - plans/**
  - reports/**
  - pyproject.toml
  - docker-compose.yml
adr: []
github:
  number: 30
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/30
```

## 背景

真实 Runner 会重启、失联、超时或被取消。没有分阶段超时、重试和对账，用例会永久停在 queued 或 running，旧回调也可能覆盖新状态。

## 目标

- 维护 queued、Agent 执行、评分、上传宽限和心跳五类超时，分别产生可诊断终态。
- 实现重试策略：环境类故障新建 attempt，配置错误快速失败，评分可单独重试，上传在宽限期内重试。
- 实现取消状态机，撤销短期 token 并请求 GitHub 取消 workflow 或 job。
- 增加对账 worker：补建丢失的完成事件、回收失联 Runner、拒绝旧 attempt 的迟到回调。

## 验收

- 超时和心跳丢失进入可诊断终态，不会永久 queued 或 running。
- 重试创建新 attempt 且不覆盖旧结果，事件与指标只取最新有效 attempt。
- 迟到回调返回 stale_attempt 并保留审计，取消事实不被覆盖。
- worker 重启、outbox 重放和对象缺失场景有自动化测试。

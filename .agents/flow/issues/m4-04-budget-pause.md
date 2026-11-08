# 实现模型预算暂停与恢复

[English](m4-04-budget-pause.en.md) | 中文

```yaml flow
kind: issue
milestone: m4
priority: P1
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
  number: 31
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/31
```

## 背景

长时间评测可能产生不可控的模型费用。达到预算时应安全暂停而不是判为失败，用户增加预算后从未完成用例继续。

## 目标

- 为 eval_run 增加预算、已花费和 budget_version，模型网关按 attempt 累计花费并检查版本。
- 实现 pausing_budget 到 paused_budget 的暂停流程：停止新派发、拒绝新模型调用、等待活跃 attempt 退出。
- 提供 resume-budget API，原子增加预算、写审计并把未完成用例重新入队。
- 预算暂停期间不生成最终结果，也不自动切换模型或资源。

## 验收

- 达到预算后不再产生新模型调用，已完成用例结果保留。
- 恢复只调度未完成用例，被打断用例创建新 attempt。
- 并发确认和重复恢复请求幂等，budget_version 不匹配时拒绝。
- 暂停、恢复和预算越界都有自动化测试。

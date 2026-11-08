# 实现发起评测向导与 prepare/create 流程

[English](m5-04-eval-launch-wizard.en.md) | 中文

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
  number: 36
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/36
```

## 背景

发起评测需要组合数据集版本、Runtime、资产、模型和资源池，并在创建前冻结配置。当前没有 prepare/create 接口和可用向导。

## 目标

- 实现 prepare 与 create API，服务端复核容量、凭据、Ref 和资产兼容性，返回 preparation_id、警告和快照摘要。
- 实现四步向导：数据集、Agent 资产与 Runtime、模型与资源、汇总确认。
- 创建使用幂等键，处理 preparation 过期、配置变化、容量非法和凭据越权。
- 临时 Key 不持久化，修改任一项使旧 preparation 失效。

## 验收

- 用户可在页面上完成一次真实评测创建，配置快照完整且可追溯。
- 同一幂等键重复提交返回同一评测，409 和 422 映射到对应字段。
- 刷新或离开页面不丢非敏感草稿，临时 Key 不落盘。
- 向导流程和视觉与原型一致，关键路径有测试。

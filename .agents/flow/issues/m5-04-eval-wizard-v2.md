# 升级发起评测向导与一致性预检

[English](m5-04-eval-wizard-v2.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P0
status: done
scope:
  - .agents/flow/issues/m5-04-eval-wizard-v2.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
  - reports/**
  - plans/**
adr: []
github:
  number: 86
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/86
```

## 背景

当前发起向导已经支持数据集、模型、Runtime 和资源池的最小闭环，但缺少 Agent 资产、Git Ref、用例子集、并发、超时、重试和预算输入，且在创建阶段没有完整复用预检快照。M4 的并发与预算语义、M5 的配置页面完成后，需要把它们组合成可验证的产品主路径。

## 目标

- 将向导扩展为数据集与用例、Agent 资产与 Ref、模型与资源、执行策略、汇总确认的完整流程。
- 支持按用例或数量选择子集，配置并发、超时、失败重试和模型预算，并展示容量警告与预计费用区间。
- 服务端返回可过期的 preparation 快照，冻结数据集版本、资产 commit、Runtime、模型、资源池和执行策略；配置变化后旧快照不可直接创建。
- 创建请求使用幂等键，字段级映射 409、422 和预检阻塞项，刷新或离开页面不丢非敏感草稿。

## 验收

- 用户无需 seed 或命令行即可选择完整评测组合并创建一次真实评测，创建记录能追溯全部冻结输入。
- 同一幂等键重复提交返回同一评测，配置变化、preparation 过期和容量非法都有对应页面提示。
- 并发、超时、重试和预算值经过后端复核，不在前端伪造默认成功；临时 Key 不落盘。
- 关键路径、空态、错误态、返回修改和移动窗口下的主操作有测试或视觉对照证据。

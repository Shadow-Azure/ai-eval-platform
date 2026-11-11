# 评测资产改为可选且 Agent 保持必选

[English](m5-09-optional-evaluation-assets.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P1
status: done
scope:
  - .agents/flow/issues/m5-09-optional-evaluation-assets.*
  - web/**
  - tests/backend/**
  - docs/**
  - reports/**
adr: []
github:
  number: 122
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/122
```

## 背景

发起评测向导当前把 Agent 资产步骤当作必经步骤：未冻结资产快照时无法进入执行策略，`准备并检查` 也被禁用。用户实际评测只使用 Runtime 内置能力时仍被迫配置 Git 资产源，导致无法发起评测。

## 目标

- 发起评测时可以不选择 Agent 资产源，直接使用 Runtime 内置能力启动评测。
- Agent Runtime 仍是必选输入；未选择 Runtime 时不能继续。
- 资产可选后，向导摘要、确认页与详情页要准确显示「未选择资产」。

## 验收

- 不选择资产源与 Ref 时，可以从第二步进入执行策略并完成准备与创建；准备结果带 `assets_empty` 警告，创建后的评测没有资产快照。
- 选择资产源并读取后，仍按原流程冻结快照并写入评测。
- 未选择 Agent Runtime 时，第二步与准备动作给出明确提示且不能继续。
- 后端测试覆盖无资产快照的准备与创建；Web 构建通过。

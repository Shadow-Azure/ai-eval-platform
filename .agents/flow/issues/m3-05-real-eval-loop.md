# 跑通单个真实用例的端到端评测

[English](m3-05-real-eval-loop.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
  - runner/**
  - runner-contract/**
  - .github/workflows/**
adr: []
github:
  number: 27
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/27
```

## 背景

数据集、模型网关、Runtime、评分和回调分别完成后，需要证明它们在同一个真实用例上组成可复现的评测闭环，而不是各自通过单元测试。

## 目标

- 提供最小评测创建与查询 API，冻结数据集版本、Runtime、资产、模型、资源池和指标版本快照。
- 用真实模型端点（MiniMax 或兼容服务）跑通一个 SWE-bench 用例，完成 Agent 执行、官方评分和结果查询。
- 复用 M2 的派发、回传与幂等契约，补齐真实任务的 manifest 和回调。
- 建立可重复的端到端 fixture 与验证报告，常规 CI 不依赖真实模型调用。

## 验收

- 一个真实用例从创建到终态成功，结果、测试明细和失败日志均可查询。
- 凭据无效、评分失败和回调丢失等路径可诊断，且不产生重复 attempt。
- 同一幂等键重复创建返回同一评测，配置变化时明确拒绝。
- 真实模型调用只在受控环境中执行，常规 CI 使用 mock 或 fixture 通过。

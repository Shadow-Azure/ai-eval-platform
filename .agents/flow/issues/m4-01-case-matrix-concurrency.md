# 实现用例级 Matrix 与有效并发调度

[English](m4-01-case-matrix-concurrency.en.md) | 中文

```yaml flow
kind: issue
milestone: m4
priority: P0
status: ready
scope:
  - .agents/flow/issues/m4-01-case-matrix-concurrency.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - runner/**
  - runner-contract/**
  - .github/workflows/**
  - tests/**
  - docs/**
  - deploy/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 78
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/78
```

## 背景

M3 已证明单个真实用例可以完成执行、评分和回传，但当前工作流仍以一个 Job 顺序执行整个 shard；多个 shard 虽可被不同 Runner 领取，用例级的并发、排队和故障隔离粒度仍然不足。评测创建也会把 `requested_concurrency` 固定为 1，预检结果没有进入真实调度。

## 目标

- 保持一次 workflow run 对应一个 shard，并在 workflow 内使用 matrix 让每个 Job 只执行一个用例。
- 将 `requested_concurrency` 定义为单个评测内同时运行用例的硬上限，结合空闲 Runner 与资源池容量计算有效并发。
- 让调度器按有效并发放行 shard 和用例，未获得容量的工作保持排队，不使用 GitHub 队列充当唯一限流器。
- 保留现有 manifest、短期 token、原子 claim 和单用例重试契约，重复 Job 必须在启动 Agent 前退出。

## 验收

- 500 个用例按默认 shard 大小 64 形成 8 次 workflow 派发，每个 matrix Job 只包含一个用例。
- 至少两台在线 Runner 时能够并行使运行用例数达到有效并发上限，超出上限的用例保持 queued 且不会重复创建 attempt。
- 重复派发、worker 重启和同一 Job 重放不产生重复执行、重复上传或回退运行状态。
- 后端状态机、workflow 结构、Runner case 入口和至少一次多 Runner 集成验证有自动化测试或报告证据。

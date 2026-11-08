# 实现分片调度、并发控制与多 Runner 派发

[English](m4-01-shard-scheduling.en.md) | 中文

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
  number: 28
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/28
```

## 背景

单用例闭环不能利用多台 Runner。M4 需要把评测切成有界分片，按在线容量与请求并发派发，并保证重复派发和乱序回调不破坏状态。

## 目标

- 实现 outbox dispatcher worker：生成分片 manifest、调用 repository_dispatch、保存 dispatch_key 对账信息。
- 按 16 到 64 的分片大小与 256 硬上限切分，按在线 Runner 数、池上限和请求并发计算有效并发。
- 支持多个临时 Runner 并发领取与执行，聚合 run 状态为 completed、partial 或 failed。
- 容量不足时保持排队并发布 resource_price_above_threshold 等明确事件。

## 验收

- N 个在线 Runner 能并行执行 N 个用例 Job，超过容量的用例保持排队。
- 重复 dispatch、重复回调和乱序回调不会产生重复 attempt 或回退状态。
- 500 个用例被切成不超过 8 个分片，每个 Job 仍然只执行一个用例。
- 分片失败可重试，对账信息可查询，调度器重启后从 outbox 恢复。

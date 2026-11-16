# [m8-37] spot 回收后重新申请竞争态资源池，最多重试三次

[English](m8-37-spot-retry-fallback.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: done
scope:
  - .agents/flow/issues/m8-37-spot-retry-fallback.*
  - .agents/flow/issues/m8-36-unified-image-sources.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/services/case_attempts.py
  - backend/app/services/provisioning/aliyun.py
  - tests/backend/**
  - plans/2026-11-16-pr258-review.md
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
adr: []
github:
  number: 258
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/258
```

## 背景

spot 实例被 `provider_stopped` 后，平台只等 300s 判 `heartbeat_timeout`；当前 `max_attempts=2` 只重试 1 次，没有专门的 spot 重试闭环。

## 目标

- provider_stopped 立即把 active attempt 标 `spot_interrupted`，不等心跳超时。
- **重新申请原竞争态 spot 池**，同一 case 最多 3 次 spot 重试。
- 第 4 次停止标 `spot_retry_exhausted`，不再创建实例。
- spot 重试计数独立于普通 `max_attempts`。
- 不默认切 fixed/on-demand；保留可选兜底配置。

## 验收

- provider stop <30s 标 `spot_interrupted`，生成 attempt+1。
- 3 次重试后第 4 次停止标 exhausted。
- 单测覆盖 3 次重试与上限；CI 通过。

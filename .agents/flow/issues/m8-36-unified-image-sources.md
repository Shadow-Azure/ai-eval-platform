# [m8-36] 统一多源镜像轮流尝试：retry manifest 与失败回调

[English](m8-36-unified-image-sources.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: done
scope:
  - .agents/flow/issues/m8-36-unified-image-sources.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/issues/m8-35-spot-duration.*
  - backend/app/services/case_attempts.py
  - runner/scripts/real-task.mjs
  - tests/backend/**
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
adr: []
github:
  number: 256
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/256
```

## 背景

无资产 spot 复测中 retry manifest 丢失 `image_sources`，重试直接拉 Docker Hub 超时；real-task 镜像失败后没有 failed 回调，平台误判 heartbeat_timeout。

## 目标

- 首次/retry manifest 字段一致，至少包含 `image_sources`、`max_turns`、`agent_timeout_seconds`。
- retry 也执行多源轮流 pull，不再静默回 Docker Hub。
- 镜像/执行失败先发 failed 回调，再退出；平台快速标 failed。
- 新增 attempt 1/2 manifest 一致性测试。

## 验收

- retry manifest 带源；镜像失败用例快速 failed；测试/CI 通过。

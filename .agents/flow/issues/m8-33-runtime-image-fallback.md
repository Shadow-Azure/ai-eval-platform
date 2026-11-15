# [m8-33] 评测运行时按镜像源顺序解析与回退

[English](m8-33-runtime-image-fallback.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-33-runtime-image-fallback.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/services/evaluations.py
  - backend/app/api/runners.py
  - runner-contract/schemas/v1/task-manifest.schema.json
  - runner/lib/task-payload.mjs
  - runner/lib/image-sources.mjs
  - runner/scripts/real-task.mjs
  - tests/backend/**
  - tests/runner/**
  - plans/2026-11-15-pr251-review.md
  - tests/runner/image-sources.test.mjs
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
adr: []
github:
  number: 250
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/250
```

## 背景

`DATASET_IMAGE_REGISTRIES` 只用于导入探测；Runner 运行时直接 `docker create swebench/...`，只走 Docker Hub。spot 冷实例无本地镜像且 Docker Hub 443 超时，2 个用例 `runtime_error`，其余排队，0 succeeded；不是所有源不可用，而是运行时没有 fallback。

## 目标

- manifest/task payload 冻结生效 `image_sources`。
- Runner 本地未命中时按源顺序构造 registry/prefix 引用并 pull；不存在、超时、慢/失败自动下一个。
- 最终源和尝试链写入 trace/诊断；全失败明确 `image_unavailable`。
- 公开源无认证；兼容旧 manifest/固定池缓存。

## 验收

- spot 冷实例 Docker Hub 超时后自动切换并成功拉起镜像。
- runner/backend 测试、CI 通过；文档与 flow 同步。

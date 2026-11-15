# [m8-34] 竞争态 spot 池零常驻与按 job 临时实例生命周期

[English](m8-34-spot-zero-resident.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m8-34-spot-zero-resident.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/issues/m8-33-runtime-image-fallback.*
  - backend/app/config.py
  - backend/app/main.py
  - .github/workflows/deploy.yml
  - deploy/docker-compose.prod.yml
  - backend/app/api/**
  - backend/app/services/capacity_control.py
  - backend/app/services/provisioning/**
  - tests/backend/**
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
adr: []
github:
  number: 252
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/252
```

## 背景

spot 实例 online、ephemeral runner 1–2 分钟退出后，容量 controller 要等 `heartbeat_timeout + lost_grace`（约 390s）才回收，且容量扫描默认 300s；5 个并发 shard 在空窗里饿死。不是价格/镜像问题。

## 目标

- 零常驻：无 warm pool、min capacity、idle runner；无 job 时 current_size=0。
- 一 job 一实例：runner 退出后实例立即关机/terminate，不留 online/offline orphan。
- 事件驱动：GitHub `workflow_job` webhook 触发 reconcile；30s 轮询兜底。
- 冷启动靠预烘焙 AMI/预拉镜像，不靠常驻。

## 验收

- 无 job 时 0 实例；5 job 并行 5 台；job 后 1 分钟内实例终止。
- 不再出现实例 online、runner offline 长时间占容量。
- 取消评测无残留 shard/实例；backend 测试/CI 通过。

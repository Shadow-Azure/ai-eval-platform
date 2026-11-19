# [m8-48] 竞争态 spot 池 warm pool 与离线 Runner 清理

[English](m8-48-spot-warm-pool-cleanup.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/**
  - backend/**
  - docs/architecture.*
  - plans/**
  - tests/**
adr: []
github:
  number: 313
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/313
```

## 背景

#306 现场：2 小时窗口内池级创建 31 台实例、29 次中断；查询时全库有 307 个 offline runner 行；典型启动到注册约 2–3 分钟，60 秒 lost grace 对短暂心跳抖动过于敏感。零常驻 spot 的启动/回收成本被放大。

## 目标

- 保留小规模 warm pool（`min_capacity≥1`）或实例复用；默认 `min_capacity=0` 不改变现有零常驻行为。
- 延长并先探测后回收 lost grace，避免刚注册或短抖动就被回收。
- 定期清理 offline runner 行，容量控制器显式忽略历史行。
- 成本上限与最小容量可配，默认不改变现有零常驻行为。

## 方案

- 容量缺口同时考虑排队需求与 warm pool 下限：`min_capacity` 是 Runner 数，无排队时也补足到该下限，并继续受 `max_capacity`、价格阈值和单批创建上限约束。
- `eval_spot_runner_lost_grace_seconds` 默认从 60 秒延长到 180 秒，并在回收前要求 Runner 持续 offline 或心跳超过 `heartbeat_timeout + grace`，已恢复的 Runner 不因一次短抖动被回收。
- 缩容跳过注册不满 `eval_spot_runner_min_idle_seconds`（默认 120 秒）的 Runner，避免刚注册就被 drain；刚结束 Job 的 Runner 不受影响。
- Worker 定期清理超过保留期的 offline Runner 行：只清理无活跃 attempt、无非终态实例引用的行；终态 attempt 与已释放实例保留历史，但释放指向被删行的外键指针。
- 容量控制器用显式状态白名单与历史状态排除过滤 Runner，offline/draining 行不参与当前/期望容量。

## 验收

- `min_capacity=1` 且无排队时自动补充 warm 实例；`min_capacity=0` 时仍为无 Job 零实例。
- Runner 心跳短暂抖动（超过 60 秒但未超过新 grace）不再触发实例回收；真实退出在 grace 到期后仍能回收。
- 注册后 120 秒内的空闲 Runner 不被缩容回收；已运行过 Job 的 Runner 仍按原逻辑回收。
- 清理任务幂等并有测试：过期的 offline 行被删除，活跃/近期行保留，第二次执行不再删除。
- 300+ 历史 offline 行不改变容量状态；`npm test`、`pytest tests/backend`、`npm run verify:flow`、`npm run verify:docs` 通过。
- 文档记录 warm pool、lost grace、清理保留期与配置项。

## 范围

- 涉及：`backend/app/config.py`、`backend/app/services/capacity_control.py`、`backend/app/services/provisioning/aliyun.py`、`backend/app/services/runners.py`、`backend/app/worker/main.py`、`tests/backend/`、`docs/architecture.*`、`plans/m1-02-fullstack-design/`。
- 不涉及：fixed 池行为、spot 连续失败后的按量/fixed 回退（#306 P2-1）、模型供应商 429、新的队列后端。

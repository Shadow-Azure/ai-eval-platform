# [m8-35] 竞价资源按评测用例超时设置 SpotDuration 保底时长

[English](m8-35-spot-duration.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: done
scope:
  - .agents/flow/issues/m8-35-spot-duration.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/issues/m8-34-spot-zero-resident.*
  - backend/app/services/capacity_control.py
  - backend/app/services/provisioning/__init__.py
  - backend/app/services/provisioning/aliyun.py
  - tests/backend/**
  - plans/2026-11-16-pr255-review.md
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
adr: []
github:
  number: 254
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/254
```

## 背景

生产 spot 实例在 job 开始 2–3 分钟后被 Aliyun 停止（`provider_stopped`），Runner 收到 shutdown 后 exit 143，平台判 heartbeat_timeout。创建参数只有 `SpotStrategy=SpotAsPriceGo`，没有 `SpotDuration`；price_threshold 只控制创建，不保证运行期。

## 目标

- 评测启动时按有效 `case_timeout_seconds` 向上取整设置 Aliyun `SpotDuration`（1–6h）。
- 实例创建时传给 RunInstances；并发批次取最大超时。
- >6h 时明确回退/报错，不创建无保底实例。
- 保持零常驻/一 job 一实例。

## 验收

- 3600s→1、5400s→2；RunInstances 带 `SpotDuration`。
- >6h 有可诊断错误，不创建无保底实例。
- 测试/CI 通过；生产 5 用例不再 2–3 分钟被停止。

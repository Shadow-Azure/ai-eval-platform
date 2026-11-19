# [m8-52] 竞争态 spot 池 SpotDuration 超过 1 小时时按 1 申请

[English](m8-52-spot-duration-cap.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-52-spot-duration-cap.*
  - backend/app/services/capacity_control.py
  - backend/app/services/provisioning/__init__.py
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
  - plans/2026-11-19-pr*-review.md
  - tests/backend/**
adr: []
github:
  number: 329
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/329
```

## 背景

生产竞争态 spot 池无法为 60 分钟 Agent 超时的评测扩容：池事件 `resource.provisioning_failed` 记录 `cloud_api_failed` / `The specified SpotDuration is not valid.`。`required_spot_duration_hours()` 把 `case_timeout_seconds=4800` 向上取整为 2，而阿里云 `RunInstances` 的 `SpotDuration` 目前只接受 0 或 1；任何 Agent 超时 3600 秒的 spot 评测都会得到 `case_timeout=4800`、申请 2 并扩容失败，run 一直停在 queued。

## 目标

- 扩容申请的 `SpotDuration` 超过 1 小时时按 1 申请，不再向阿里云发送 2–6。
- 保留 0/1 语义：spot 实例仍申请 1 小时保护期；超过 1 小时后按阿里云规则可能被回收，由现有 spot 回收重试/回退机制兜底。
- 补单测覆盖 `case_timeout` 为 3600、4800 与 >6 小时的情况（均应为 1），并同步更新相关注释与设计文档。

## 验收

- `case_timeout_seconds=4800` 的 spot 扩容请求发送 `SpotDuration=1`，不再出现 `InvalidSpotDuration`。
- `case_timeout_seconds` 为 3600、4800、>6 小时的测试均返回 1；扩容路径实际交给 provisioner 的 spec 也是 1。
- `pytest tests/backend`、`npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。

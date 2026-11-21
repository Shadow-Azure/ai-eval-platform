# 用例完成即上报 bad case

[English](m9-per-case-badcase-report.en.md) | 中文

```yaml flow
kind: issue
milestone: m9
priority: P0
status: done
scope:
  - backend/app/**
  - tests/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 288
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/288
```

## 背景

当前 bad case 只在评测 run 终态统一入队，长评测里先完成的 bad case 要等最后一个用例结束才到达 harness-evolution，闭环反馈被整体推迟；`self_evolution_report` 本身是 outbox，HE 也允许任意时刻提交单条 bad case，因此可以在结果不可变后按用例提前入队。

## 目标

- Worker 在每轮 trace 解析发布后增加逐用例对账 pass；`succeeded` 且非 resolved 的用例立即入队，`failed`/`timeout` 只在该用例重试预算耗尽后入队。
- 只处理 trace 状态已到终态且最终 attempt 登记了可解析轨迹对象的用例；trace 仍 pending、无轨迹、已 resolved、已取消或取消中的 run 不入队。
- 自动重试创建新 attempt 时把 `trace_status` 重置为 pending，避免失败重试用旧 attempt 的终态 badge 被提前上报。
- run 终态保留现有批量入队作为兜底；入队继续使用唯一键与 savepoint 保证幂等。
- 同步架构文档与全栈设计中的上报时序，补齐单测。

## 验收

- 运行中的 run 内，一个 `succeeded + unresolved` 用例在 worker pass 后生成 pending 行，并能在同一轮 POST。
- `failed` 用例在 attempt 未耗尽时不入队，耗尽后入队；`succeeded + resolved`、`cancelled` 用例、`cancelled/cancelling` run、无轨迹用例不入队。
- `trace_status=pending` 时等待，trace 到终态后入队且配置齐全时带 `sessionSource`；自动重试后 `trace_status=pending`，最终 attempt 无轨迹时修正为 unlinked 且不上报。
- 重复调用只生成一行；现有 `tests/backend/test_self_evolution_report.py` 全绿并新增上述覆盖。
- `docs/architecture.md` 与设计 04/05 改为“用例结果不可变后逐条入队 + run 终态兜底”；相关门禁通过。

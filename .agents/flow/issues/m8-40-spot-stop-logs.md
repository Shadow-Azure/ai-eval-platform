# [m8-40] 补充 spot runner 与模型 token 失败日志

[English](m8-40-spot-stop-logs.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-40-spot-stop-logs.*
  - .agents/flow/issues/m8-41-model-token-ttl.*
  - backend/app/services/provisioning/aliyun.py
  - runner/scripts/real-task.mjs
  - tests/backend/test_aliyun_provisioner.py
  - tests/runner/heartbeat-integration.test.mjs
adr: []
github:
  number: 268
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/268
```

## 背景

生产 v3（run `4c9e69d4-f0df-4574-84a4-80111a6c8b18`）的 26 个 spot 实例中，带 `spot_interrupted` 的 attempt 都落在 `reclaimed_reason=runner_exited` 实例上；`spot_duration_hours=1`，阿里云文档保证创建后 1 小时内不自动释放，实例却 1–8 分钟就停。当前日志只能看到 `provider_stopped`/`Stopped`，无法区分：

- 阿里云真实回收；
- 单 job runner 退出后 `ExecStopPost=shutdown` 自关机；
- 容量控制器缩容；
- terminal callback 与 provider 状态更新的先后顺序。

## 目标

- 平台 `_reclaim` 持久化 `stop_context`：provider 状态、实例规格/可用区/价格/spot_duration、runner last heartbeat、attempt 状态/错误码、case 状态、是否已收到 terminal callback。
- 资源事件 payload 带 `stop_context`，便于按 instance/attempt 查询。
- runner 在 completed/failed 回调成功后输出 `terminal_callback_sent`，便于与平台状态对时序。
- 保持现有行为不变，仅增加观测；后续用这些日志决定 stop_origin 判定修复。

## 验收

- 新测试断言 `provisioned_instance.cost_json.stop_context` 包含 attempt/runner/provider 字段，事件 payload 同步。
- runner 日志在回调成功/失败后都有 `terminal_callback_sent`，包含 case/attempt/status/callback status。
- 现有回收/重试行为测试全部通过。

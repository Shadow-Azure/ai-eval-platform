# [m8-39] 修复 spot 回收映射与运行中 runner 被回收

[English](m8-39-spot-reclaim-binding.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-39-spot-reclaim-binding.*
  - backend/app/services/provisioning/aliyun.py
  - backend/app/api/runners.py
  - backend/app/services/capacity_control.py
  - tests/backend/test_aliyun_provisioner.py
  - tests/backend/test_case_callbacks.py
  - tests/backend/test_capacity_control.py
adr: []
github:
  number: 264
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/264
```

## 背景

生产复测 eval `1c670369-cd7f-4597-b32e-9bdffd51ea2e` 与 `d4f56ba0-dcd1-4030-97a6-b4d00752e0ab` 表明 #262 之后快速重试仍未触发，存在两个独立问题：

- `_reclaim` 只在 `instance.runner_id` 非空时查找 active attempt。实例在 sync 关联 Runner 之前被云侧停止时，`instance.runner_id` 仍为 NULL；`provider_stopped` 只产生 `resource.instance.interrupted`，不产生 `case.spot_interrupted`，仍会等 300 秒心跳超时。
- spot runner 的 `busy_case_attempt_id` 始终为空（cloud-init 心跳只发 `online`，case 回调不带 `X-Runner-Id`），容量控制器把运行中的 runner 当成 idle；单用例运行中 queued=0 时会 drain/release 该实例。

## 目标

- `provider_stopped` 即使发生在 Runner 关联之前，也能通过 `cost_json.runner_name` 与 active attempt 命中并快速重试。
- started 回调把解析到的 Runner 绑定到 case；容量控制器不把有 busy 绑定的 runner 当 idle，也不在 scale-down 中选择它。
- 回归测试覆盖上述两条路径。

## 验收

- 生产停止 spot 实例后 30 秒内出现 `case.spot_interrupted` 并创建 attempt+1，不出现 `heartbeat_timeout`。
- 单用例 spot 运行中不再出现容量控制器 drain/release 该 runner。
- 新增测试通过；backend pytest 与 ruff 通过。

# [m8-53] Runner SIGTERM 现场诊断与 stop evidence

[English](m8-53-runner-sigterm-observability.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-53-runner-sigterm-observability.*
  - .agents/flow/issues/m8-53-token-usage-stats.*
  - backend/app/services/provisioning/aliyun.py
  - runner/lib/diagnostics.mjs
  - runner/scripts/real-task.mjs
  - tests/backend/test_aliyun_provisioner.py
  - tests/sigterm-diagnostics.test.mjs
adr: []
github:
  number: 334
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/334
```

## 背景

生产评测 2739aa67 中，astropy__astropy-13236 与 astropy__astropy-13398 均以 `runner_lost` / 「Runner 收到 SIGTERM」失败；平台没有记录 stop origin，无法判断是云侧停止、系统维护、人工 Stop 还是 Runner 进程/VM 本地退出。Runner 侧也只有一句 SIGTERM 日志，没有 systemd/journal/dmesg/metadata 现场；实例释放后无法回看。

PR 初版曾把 `runner_lost` 设为可重试，但用户指出当前现象明显不是低概率：在根因未定位前重试只会重复消耗实例和时间。因此本 issue 撤回自动重试，先补现场证据；只有在根因确认不可避免且低概率时，才另行讨论重试。

## 目标

- Runner SIGTERM 时输出结构化诊断（systemd/journal/dmesg/metadata/GitHub 环境），并作为 `artifact_kind=artifact` 上传 `diagnostics/sigterm.json`。
- 平台 reclaim 时把 `OperationLocks`、实例状态/年龄、spot duration/termination time、attempt 阶段/起止、case key、github_run_id 写入 `provisioned_instance.cost_json["stop_context"]` 和 `resource_event`；不新增列、不迁移。
- 保持既有重试语义不变；本 issue 不引入 blanket retry。
- 为后续 GitHub job evidence、CloudMonitor/ActionTrail、metadata 轮询与 UI 告警留下字段/接口。

## 验收

- SIGTERM handler 打印 `{"status":"sigterm_diagnostics",...}`，uploads 回调包含 `diagnostics/sigterm.json`（artifact_kind=artifact），失败回调仍能发出。
- 失败实例的 `stop_context` 包含 `operation_locks`、`instance_status`、`instance_age_seconds`、`attempt_phase`、`attempt_started_at`、`case_key`、`github_run_id`；`resource_event.payload_json.stop_context` 同步包含；无迁移。
- `handle_failed` 的重试语义保持原样；`runner_lost + retryable=false` 仍按 Runner 的字段结算，不因本 issue 自动重试。
- `pytest tests/backend`、`npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。
- 非目标：自动重试、GitHub job API、CloudMonitor/ActionTrail 轮询、metadata 提前预警、UI/告警，作为后续 issue 拆分。

# [m8-53] Runner SIGTERM 可观测性与 runner_lost 可重试

[English](m8-53-runner-sigterm-observability.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-53-runner-sigterm-observability.*
  - backend/app/services/case_attempts.py
  - backend/app/services/provisioning/aliyun.py
  - runner/lib/diagnostics.mjs
  - runner/scripts/real-task.mjs
  - tests/backend/test_aliyun_provisioner.py
  - tests/backend/test_case_callbacks.py
  - tests/sigterm-diagnostics.test.mjs
adr: []
github:
  number: 334
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/334
```

## 背景

生产评测 2739aa67 中，astropy__astropy-13236 与 astropy__astropy-13398 均以 `runner_lost` / 「Runner 收到 SIGTERM」失败；平台没有记录 stop origin，无法判断是云侧回收、维护、人工 Stop 还是 Runner 进程退出。13236 的 SIGTERM 回调携带 `retryable=false`，导致 2m02s 就死亡的 attempt 一次都不重试；Runner 侧也只有一句 SIGTERM 日志，没有 systemd/journal/dmesg/metadata 现场，实例释放后无法回看。

## 目标

- `runner_lost` 的外部终止在未耗尽 case 预算时按可重试结算；仍受 `max_attempts`、run cancelling/paused 与 case 预算约束。
- Runner SIGTERM 时输出结构化诊断（systemd/journal/dmesg/metadata/GitHub 环境），并作为 `artifact_kind=artifact` 上传 `diagnostics/sigterm.json`。
- 平台 reclaim 时把 `OperationLocks`、实例状态/年龄、spot duration/termination time、attempt 阶段/起止、case key 写入 `provisioned_instance.cost_json["stop_context"]` 和 `resource_event`；不新增列、不迁移。
- 为后续 GitHub job evidence、CloudMonitor/ActionTrail、metadata 轮询与 UI 告警留下字段/接口。

## 验收

- `runner_lost` + `retryable=false` 的失败回调，在 running 且 elapsed < case_timeout 时生成 attempt 2；达到 max_attempts 不再生成 attempt 3；cancelling/paused run 不重试。
- SIGTERM handler 打印 `{"status":"sigterm_diagnostics",...}`，uploads 回调包含 `diagnostics/sigterm.json`（artifact_kind=artifact），失败回调仍能发出。
- 3 台失败实例的 `stop_context` 包含 `operation_locks`、`instance_status`、`instance_age_seconds`、`attempt_phase`、`attempt_started_at`；`resource_event.payload_json.stop_context` 同步包含；无迁移。
- `pytest tests/backend`、`npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。
- 非目标：GitHub job API、CloudMonitor/ActionTrail 轮询、metadata 提前预警、UI/告警，作为后续 issue 拆分。

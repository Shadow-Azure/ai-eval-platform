# [m8-53] Runner SIGTERM observability and retryable runner_lost

English | [中文](m8-53-runner-sigterm-observability.md)

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

## Background

In production evaluation 2739aa67, astropy__astropy-13236 and astropy__astropy-13398 both failed with `runner_lost` / "Runner received SIGTERM"; the platform recorded no stop origin and cannot tell cloud reclaim, maintenance, manual stop, or runner process exit. The 13236 cancellation callback carried `retryable=false`, so a 2m02s attempt was never retried; the runner emitted only one SIGTERM line without systemd/journal/dmesg/metadata, and the instance was released before any evidence could be collected.

## Goals

- Treat an external `runner_lost` termination as retryable while the case budget is not exhausted; still honor `max_attempts`, cancelling/paused runs, and the case budget.
- On SIGTERM, emit structured diagnostics (systemd/journal/dmesg/metadata/GitHub env) and upload `diagnostics/sigterm.json` as `artifact_kind=artifact`.
- On reclaim, record `OperationLocks`, instance status/age, spot duration/termination time, attempt phase/start/end and case key in `provisioned_instance.cost_json["stop_context"]` and `resource_event`; no new columns or migrations.
- Leave fields/interfaces for follow-up GitHub job evidence, CloudMonitor/ActionTrail, metadata polling and UI alerts.

## Acceptance

- A `runner_lost` + `retryable=false` failure on a running attempt with elapsed < case_timeout queues attempt 2; at max_attempts no attempt 3 is created; cancelling/paused runs do not retry.
- The SIGTERM handler prints `{"status":"sigterm_diagnostics",...}`; the uploads callback contains `diagnostics/sigterm.json` (artifact_kind=artifact); the failure callback is still sent.
- The three failed instances' `stop_context` includes `operation_locks`, `instance_status`, `instance_age_seconds`, `attempt_phase`, `attempt_started_at`; `resource_event.payload_json.stop_context` mirrors them; no migration.
- `pytest tests/backend`, `npm test`, `npm run verify:flow`, `npm run verify:docs` pass.
- Non-goals: GitHub job API, CloudMonitor/ActionTrail polling, metadata preemption watch, UI/alerting; split into follow-up issues.

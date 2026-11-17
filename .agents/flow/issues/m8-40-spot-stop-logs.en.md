# [m8-40] Add spot runner and model token failure logs

English | [中文](m8-40-spot-stop-logs.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-40-spot-stop-logs.*
  - .agents/flow/issues/m8-41-model-token-ttl.*
  - backend/app/services/provisioning/aliyun.py
  - runner/scripts/real-task.mjs
  - tests/backend/test_aliyun_provisioner.py
adr: []
github:
  number: 268
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/268
```

## Background

In production v3 (run `4c9e69d4-f0df-4574-84a4-80111a6c8b18`), all `spot_interrupted` attempts map to instances with `reclaimed_reason=runner_exited`; `spot_duration_hours=1` and Aliyun guarantees no automatic release within the first hour, yet instances stopped after 1–8 minutes. Current logs only expose `provider_stopped`/`Stopped`, so we cannot distinguish:

- a real Aliyun reclaim;
- a single-job runner exiting and `ExecStopPost=shutdown` powering the instance off;
- a capacity-controller scale-down;
- the ordering between the terminal callback and the provider state update.

## Goals

- `_reclaim` persists a `stop_context`: provider state, instance type/zone/price/spot_duration, runner last heartbeat, attempt status/error, case status, and whether a terminal callback was seen.
- Resource-event payload carries `stop_context` so it can be queried per instance/attempt.
- The runner logs `terminal_callback_sent` after a successful completed/failed callback, so platform state can be correlated with the job timeline.
- Keep behavior unchanged; this is observability only. A later change will use these logs to decide the stop-origin fix.

## Acceptance

- A new test asserts `provisioned_instance.cost_json.stop_context` contains attempt/runner/provider fields and the event payload mirrors it.
- Runner logs `terminal_callback_sent` after both completed and failed callback paths with case/attempt/status/callback status.
- Existing reclaim/retry behavior tests continue to pass.

# [m8-39] Fix spot reclaim mapping and running-runner retirement

English | [中文](m8-39-spot-reclaim-binding.md)

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

## Background

Production verification evals `1c670369-cd7f-4597-b32e-9bdffd51ea2e` and `d4f56ba0-dcd1-4030-97a6-b4d00752e0ab` showed that after #262 the fast retry still does not trigger. Two independent defects remain:

- `_reclaim` only looks up an active attempt when `instance.runner_id` is set. If the provider stops the instance before sync links the Runner, `instance.runner_id` is still NULL; `provider_stopped` only records `resource.instance.interrupted`, never `case.spot_interrupted`, and the case waits for the 300-second heartbeat timeout.
- The spot runner's `busy_case_attempt_id` is always empty (the cloud-init heartbeat sends only `online`, and case callbacks do not carry `X-Runner-Id`), so the capacity controller treats a running runner as idle; with queued=0 for a single running case it drains and releases the instance.

## Goals

- Map `provider_stopped` to the active attempt even before the Runner link, using `cost_json.runner_name` and the active attempt.
- Bind the resolved Runner to the case in the started callback; make the capacity controller ignore busy-bound runners for the idle count and scale-down selection.
- Cover both paths with regression tests.

## Acceptance

- In production, a stopped spot instance produces `case.spot_interrupted` and attempt+1 within 30 seconds, with no `heartbeat_timeout`.
- A single running spot case no longer gets its runner drained or released by the capacity controller.
- New tests pass; backend pytest and ruff pass.

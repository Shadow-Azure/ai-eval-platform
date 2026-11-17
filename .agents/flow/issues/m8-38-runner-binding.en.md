# [m8-38] Fix the instance→Runner→attempt binding for spot interruption retries

English | [中文](m8-38-runner-binding.md)

```yaml flow
kind: issue
milestone: m8
priority: P1
status: done
scope:
  - .agents/flow/issues/m8-38-runner-binding.*
  - .agents/flow/issues/m8-37-spot-retry-fallback.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/services/runners.py
  - runner/scripts/real-task.mjs
  - tests/backend/test_runners.py
  - tests/runner/heartbeat-integration.test.mjs
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
adr: []
github:
  number: 261
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/261
```

## Background

m8-37 fast retry does not fire: the running attempt has no `runner_id`, and `busy_case_attempt_id` is cleared by the online heartbeat, so `_reclaim` cannot map the active attempt.

## Goal

- Started callback uses `RUNNER_ID || RUNNER_NAME`, storing `attempt.runner_id`.
- Online heartbeat preserves the busy binding; only terminal/offline clears it.
- Regression tests cover the runner-id fallback and binding retention.

## Acceptance

- `attempt.runner_id` is set; online heartbeat keeps the busy binding; provider_stopped finds the attempt; CI passes.

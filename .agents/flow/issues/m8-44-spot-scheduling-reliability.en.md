# [m8-44] Contended spot pool scheduling reliability: retry callbacks, failure settlement, runner exit classification and capacity accounting

English | [中文](m8-44-spot-scheduling-reliability.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-44-spot-scheduling-reliability.*
  - .github/workflows/eval-shard.yml
  - backend/app/services/case_attempts.py
  - backend/app/services/capacity.py
  - backend/app/services/lifecycle.py
  - backend/app/services/provisioning/aliyun.py
  - backend/app/services/workbench.py
  - docs/architecture.*
  - plans/m1-02-fullstack-design/**
  - plans/2026-11-19-pr*-review.md
  - reports/**
  - runner-contract/schemas/v1/failure.schema.json
  - runner/scripts/real-task.mjs
  - scripts/doc-budgets.manifest.json
  - tests/backend/test_aliyun_provisioner.py
  - tests/backend/test_case_callbacks.py
  - tests/backend/test_capacity_scheduling.py
  - tests/backend/test_eval_workbench.py
  - tests/backend/test_failure_classification.py
  - tests/backend/test_spot_interruption.py
  - tests/eval-shard-workflow.test.mjs
adr: []
github:
  number: 306
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/306
```

## Background

A production m9 spot × SuperPower evaluation exposed four scheduling reliability defects unrelated to model 429s: manual retry wrote its platform event at sequence 0 on the NEW attempt, colliding with the Runner's `case.started` and turning started into a 500; an execute job that failed before `started` had no callback, so the platform waited the full 3600-second queue timeout; `runner_exited`/`runner_heartbeat_lost` were treated as `provider_stopped` with a fixed error message and consumed spot retry budget; and two evaluations sharing one pool each counted the whole pool, so busy runners were counted as available capacity twice. The spot retry budget was also independent of `max_attempts`, producing attempt 4.

## Goals

- Give manual retry's `case.retry_requested` a platform-reserved positive sequence so it never occupies the Runner's `case.started` sequence 0; on a started sequence collision, relocate historical platform events into the reserved range, read the stored event, return idempotently or a diagnosable 409 instead of a 500.
- When an execute job fails before started (scripts bundle, checkout, dispatch validation or execution), call back `runner_bootstrap_failed` with the manifest endpoint's short token; the platform settles it immediately under the normal retry budget.
- Only a provider-confirmed stop consumes the spot interruption path; `runner_exited`/`runner_heartbeat_lost` settle as `runner_lost`, preserving the real reason, provider_state and last_heartbeat without consuming spot retries.
- Pool-level in-flight reservation: a second evaluation sees only capacity the first evaluation has not claimed; a hard pool cap at started keeps concurrent Agents at or below `max_capacity`.
- Spot retries are bounded by both `SPOT_RETRY_LIMIT` and the run's `max_attempts`, so `attempt_no` never exceeds the declared cap.

## Acceptance

- After a manual retry, attempt 2 `started` returns 200 and can complete; a historical `retry_requested` sequence collision is migrated automatically and started succeeds.
- `runner_bootstrap_failed` before started settles as a retryable failure and creates attempt 2 without waiting for queue timeout.
- `runner_exited` on a Running instance records `runner_lost` with the real reason and retries under `max_attempts`; no `case.spot_interrupted` event appears and the spot retry counter does not grow.
- Two evaluations share a pool: while the first has five in-flight attempts the second has effective=0 and releases no shard; after one slot frees the second releases exactly one shard.
- With `max_attempts=2`, spot retries produce at most attempt 2.
- `pytest tests/backend`, `npm test`, `npm run verify:flow` and the runner contract tests pass.
- This change excludes #307's shard progress lease, spot→fixed fallback, warm pool, pre-bundled scripts and infra reporting; those remain later phases of issue #306.

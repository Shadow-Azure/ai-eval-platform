# [m8-46] Fall back to fixed/on-demand capacity after repeated spot reclaims

English | [中文](m8-46-spot-fallback-fixed.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/**
  - backend/**
  - docs/**
  - plans/**
  - tests/**
  - web/**
adr: []
github:
  number: 311
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/311
```

## Background

In the #306 production window, all four attempts of case 13236 failed on spot reclaims and ended as `spot_retry_exhausted`; 31 spot instances and 29 interruptions in the same window show that spot availability can stay bad for a long time. The current retry path only asks the same spot pool for another instance and has no fallback once the budget is exhausted.

## Goals

- After N consecutive provider-side spot reclaims in one run, or N consecutive capacity ticks with missing inventory / a price above the threshold, switch the run to an existing fixed/on-demand pool.
- N, the fallback pool and an optional cost cap are configurable; add no cloud provider and create no new pool automatically.
- Record the fallback in the run configuration snapshot and resource events, show "已回退" on the evaluation detail page, and stop ending directly as `spot_retry_exhausted`.
- Keep the existing spot retry behavior when no fallback pool is configured.

## Plan

- Add `fallback_pool_id`, `fallback_after` and `fallback_max_price_cny` to the spot pool's `instance_config`; the fallback pool must be an active fixed/on-demand pool in the same tenant.
- A `spot_fallback` service tracks per-run consecutive failure and supply-block counts; a successful case resets them. At the threshold it switches `eval_run.resource_pool_id` to the fallback pool and rewrites the `runs_on` of not-yet-dispatched outbox/manifest rows plus the pool reference of pending attempts.
- A run that has fallen back no longer spends the spot-retry budget and is bounded only by `max_attempts`; the retry is built after the switch, so its manifest, attempt and dispatch all target the fallback pool.
- Write the `spot_fallback` state into the run snapshot; add run-level `eval.spot_fallback` and pool-level `resource.spot.fallback` / `resource.spot.fallback_suppressed` events.
- When a cost cap is enabled, the fallback pool must declare `reference_price_cny` (or an existing price); an unknown or above-cap cost suppresses the fallback, records the reason and keeps the existing retry path.
- Add a resource-fallback row to the evaluation detail's execution panel; pool create/edit supports selecting the fallback pool, threshold and cap, the detail drawer shows the configuration, and the resource event list recognizes the new event types.

## Acceptance

- Forcing three consecutive provider-side spot reclaims switches the case to the fixed/on-demand pool and continues to completion; the run shows "已回退" and never reports `spot_retry_exhausted`.
- Missing inventory or a price above the threshold for the configured number of ticks also switches the run pool; jobs already sent to the old spot pool are not lost and later retries use the new pool.
- When the fallback cost is above the cap or unknown, the fallback is suppressed, a stable reason is written to resource events, and the existing spot retries continue.
- A successful case resets the consecutive-failure count; with no fallback pool configured the behavior is unchanged.
- `pytest tests/backend`, `npm test`, `npm run verify:flow`, `npm run verify:docs` and the web build pass.

## Scope

- Involves: `backend/app/config.py`, `backend/app/services/spot_fallback.py`, `backend/app/services/case_attempts.py`, `backend/app/services/capacity_control.py`, `backend/app/services/workbench.py`, `backend/app/api/resources.py`, `web/src/api/`, `web/src/pages/`, `tests/backend/`, `tests/integration/`, `docs/`, `plans/`.
- Does not involve: a new cloud provider, automatic fixed-pool creation, market bidding strategy, model provider 429, or the failure classification and capacity accounting already delivered by #306.

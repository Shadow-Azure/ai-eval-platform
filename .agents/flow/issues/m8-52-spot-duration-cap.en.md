# [m8-52] Request a 1-hour SpotDuration when the spot pool needs more than one hour

English | [中文](m8-52-spot-duration-cap.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-52-spot-duration-cap.*
  - backend/app/services/capacity_control.py
  - backend/app/services/provisioning/__init__.py
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
  - plans/2026-11-19-pr*-review.md
  - tests/backend/**
adr: []
github:
  number: 329
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/329
```

## Background

The production contended spot pool cannot scale for evaluations with a 60-minute Agent timeout: the pool event `resource.provisioning_failed` records `cloud_api_failed` / `The specified SpotDuration is not valid.`. `required_spot_duration_hours()` rounds `case_timeout_seconds=4800` up to 2, while Aliyun `RunInstances` currently accepts only 0 or 1 for `SpotDuration`; every spot evaluation with a 3600-second Agent timeout gets `case_timeout=4800`, requests 2 and fails to scale, so the run stays queued.

## Goals

- Request 1 when the computed `SpotDuration` exceeds one hour; stop sending 2–6 to Aliyun.
- Keep 0/1 semantics: spot instances still request the one-hour protection; a reclaim after that hour is handled by the existing spot interruption retry/fallback path.
- Cover 3600, 4800 and >6 hours with unit tests that all return 1, and update the related comments and design doc.

## Acceptance

- A spot scale-up with `case_timeout_seconds=4800` sends `SpotDuration=1` and no longer hits `InvalidSpotDuration`.
- Tests for `case_timeout_seconds` 3600, 4800 and >6 hours all return 1; the scale-up path hands the provisioner a spec with 1.
- `pytest tests/backend`, `npm test`, `npm run verify:flow` and `npm run verify:docs` pass.

# [m8-35] Set Aliyun SpotDuration from the evaluation case timeout

English | [中文](m8-35-spot-duration.md)

```yaml flow
kind: issue
milestone: m8
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m8-35-spot-duration.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/issues/m8-34-spot-zero-resident.*
  - backend/app/services/capacity_control.py
  - backend/app/services/provisioning/__init__.py
  - backend/app/services/provisioning/aliyun.py
  - tests/backend/**
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
adr: []
github:
  number: 254
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/254
```

## Background

Production spot instances were stopped 2–3 minutes into a job (`provider_stopped`); the runner exited 143 and the platform marked heartbeat_timeout. Creation only uses `SpotStrategy=SpotAsPriceGo`, no `SpotDuration`; price_threshold gates creation, not runtime.

## Goal

- At evaluation start, round the effective `case_timeout_seconds` up to Aliyun `SpotDuration` (1–6h).
- Pass it to RunInstances; concurrent batch uses the maximum timeout.
- >6h returns an actionable error/fallback instead of an unprotected instance.
- Keep zero-resident / one instance per job.

## Acceptance

- 3600s→1, 5400s→2; RunInstances carries `SpotDuration`.
- >6h is diagnosable and does not create an unprotected instance.
- Tests/CI pass; production 5-case run no longer stops at 2–3 minutes.

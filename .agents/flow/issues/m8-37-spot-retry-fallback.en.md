# [m8-37] Re-request the spot pool after interruption, up to three retries

English | [中文](m8-37-spot-retry-fallback.md)

```yaml flow
kind: issue
milestone: m8
priority: P1
status: done
scope:
  - .agents/flow/issues/m8-37-spot-retry-fallback.*
  - .agents/flow/issues/m8-36-unified-image-sources.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/services/case_attempts.py
  - backend/app/services/provisioning/aliyun.py
  - tests/backend/**
  - plans/2026-11-16-pr258-review.md
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
adr: []
github:
  number: 258
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/258
```

## Background

After a spot instance is `provider_stopped`, the platform waits 300s and reports heartbeat_timeout; the current `max_attempts=2` allows only one retry.

## Goal

- Mark the active attempt `spot_interrupted` immediately, not after the heartbeat timeout.
- **Re-request the same spot pool**, up to three spot retries per case.
- On the fourth stop mark `spot_retry_exhausted` and stop creating instances.
- Count spot retries separately from normal `max_attempts`.
- No default fixed/on-demand fallback; keep an optional config.

## Acceptance

- Provider stop marks `spot_interrupted` in <30s and creates attempt+1.
- After three retries the fourth stop marks exhausted.
- Unit test covers three retries and the cap; CI passes.

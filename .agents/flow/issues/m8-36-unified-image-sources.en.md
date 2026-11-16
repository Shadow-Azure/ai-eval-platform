# [m8-36] Unified multi-source image fallback: retry manifest and failure callback

English | [中文](m8-36-unified-image-sources.md)

```yaml flow
kind: issue
milestone: m8
priority: P1
status: done
scope:
  - .agents/flow/issues/m8-36-unified-image-sources.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/issues/m8-35-spot-duration.*
  - backend/app/services/case_attempts.py
  - runner/scripts/real-task.mjs
  - tests/backend/**
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
adr: []
github:
  number: 256
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/256
```

## Background

In the no-asset spot run the retry manifest lost `image_sources`, so retries went straight to Docker Hub and timed out; real-task exited without a failed callback, so the platform reported heartbeat_timeout.

## Goal

- First/retry manifests share the same fields, including `image_sources`, `max_turns`, `agent_timeout_seconds`.
- Retries also rotate across sources instead of falling back to Docker Hub.
- Image/exec failures post a failed callback before exit.
- Add attempt 1/2 manifest parity test.

## Acceptance

- Retry manifest carries sources; image failures fail fast; tests/CI pass.

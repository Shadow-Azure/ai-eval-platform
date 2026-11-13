# [m8-12] Fix heartbeat_timeout caused by the 300s case-token TTL

English | [中文](m8-12-case-token-ttl.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-12-case-token-ttl.*
  - .agents/flow/issues/m8-11-eval-skip-init-snapshot-injection.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - backend/app/api/runners.py
  - tests/backend/**
  - plans/**
adr: []
github:
  number: 206
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/206
```

## Background

- Production evaluation de656d88 (CodeSpec asset flow, after the m8-11 fix): asset injection succeeded (16 files) and the agent ran for about 4.5 minutes, but the evaluation failed at 571s with `heartbeat_timeout` (“no Runner heartbeat within 300s”) and the trace was not uploaded.
- The case token (`scope=case`) issued for shard dispatch has a hardcoded 300s TTL (`backend/app/api/runners.py:615`); the Runner uses it for heartbeats and uploads. Once it expires the backend rejects heartbeats and the platform records a heartbeat timeout.
- This is unrelated to the configured case timeout (3600s by default): any case longer than 5 minutes fails and loses its trace, so the long design→propose→apply flow cannot be validated.

## Goals

- Make the case token lifetime cover case execution: at least `case_timeout_seconds` plus a margin (e.g. 900s), or make it configurable/refreshable.
- Keep it secure: still a short-lived signed credential valid only for the case lifecycle, without affecting other tokens such as the runner scripts token.
- Tests: with a long `case_timeout_seconds` the token expires well beyond 300s; existing dispatch/heartbeat auth tests stay green.

## Acceptance

- A case can run longer than 5 minutes (up to the configured timeout) with heartbeats, uploads and trace capture working.
- Unit tests cover the TTL computation and the long-timeout case; relevant backend tests pass.
- After deployment, re-run the production evaluation and confirm design→propose→apply with a trace.

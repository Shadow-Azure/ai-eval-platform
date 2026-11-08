# Add timeouts, cancellation, retries and recovery reconciliation

English | [中文](m4-03-timeout-retry-recovery.md)

```yaml flow
kind: issue
milestone: m4
priority: P0
status: backlog
scope:
  - .agents/flow/**
  - backend/**
  - runner/**
  - runner-contract/**
  - tests/**
  - deploy/**
  - .github/workflows/**
  - docs/**
  - plans/**
  - reports/**
  - pyproject.toml
  - docker-compose.yml
adr: []
github:
  number: 30
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/30
```

## Background

Real runners restart, disconnect, time out and get cancelled. Without staged timeouts, retries and reconciliation, cases remain queued or running forever and stale callbacks may overwrite newer state.

## Goals

- Track queued, agent execution, scoring, upload grace and heartbeat timeouts, each producing a diagnosable terminal state.
- Implement the retry policy: new attempts for environment faults, fail-fast for configuration errors, separate scoring retries and upload retries within the grace period.
- Implement the cancellation state machine, revoking short-lived tokens and requesting GitHub workflow or job cancellation.
- Add a reconciliation worker that recreates lost completion events, reclaims disconnected runners and rejects late callbacks for old attempts.

## Acceptance

- Timeouts and lost heartbeats reach a diagnosable terminal state instead of staying queued or running forever.
- Retries create new attempts without overwriting old results, and events and metrics use only the latest valid attempt.
- Late callbacks return stale_attempt with an audit record and never overwrite a cancellation.
- Worker restarts, outbox replay and missing artifacts are covered by automated tests.

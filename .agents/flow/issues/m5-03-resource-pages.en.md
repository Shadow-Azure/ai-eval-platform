# Build connection and resource pool pages

English | [中文](m5-03-resource-pages.md)

```yaml flow
kind: issue
milestone: m5
priority: P1
status: backlog
scope:
  - .agents/flow/**
  - web/**
  - backend/**
  - tests/**
  - package.json
  - package-lock.json
  - docs/**
  - plans/**
  - reports/**
adr: []
github:
  number: 35
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/35
```

## Background

Connections, spot price, capacity and runner heartbeats must be visible in the UI; otherwise users cannot tell why an evaluation is queued or cannot be provisioned.

## Goals

- Build connection and resource pool APIs and pages showing provisioning mode, price threshold, capacity and online runners.
- Build a provisioning test with a cost warning and second confirmation, writing an audit record.
- Validate connection capability against pool type, rejecting mismatches with an explanation.
- Show runner labels, last heartbeat, current job and offline reason.

## Acceptance

- Creating a pool with mismatched connection capability is rejected with a clear reason.
- The pool page shows current price, threshold, online runners, last heartbeat and next check time.
- Provisioning tests require confirmation, write an audit record and never incur silent cost.
- Empty, error and unauthorized states are complete.

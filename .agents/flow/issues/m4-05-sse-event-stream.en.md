# Stream evaluation events over SSE with replay

English | [中文](m4-05-sse-event-stream.md)

```yaml flow
kind: issue
milestone: m4
priority: P1
status: backlog
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
adr: []
github:
  number: 32
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/32
```

## Background

The UI needs low-latency updates for queueing, execution, scoring and capacity, and progress must not be lost or move backwards after refresh or reconnect. SSE is only a notification; the database event cursor is the recovery source.

## Goals

- Provide paginated history events and an SSE endpoint supporting Last-Event-ID replay and resync_required for stale cursors.
- Define run, case, metric, capacity and notice event types with sanitized DTOs that carry only change summaries.
- Use Redis only for wake-ups while reading event bodies from the database by cursor, so a Redis restart loses no facts.
- Send a final event at the terminal state, then stop reconnection, applying events monotonically by case, attempt and sequence.

## Acceptance

- Refresh and reconnect lose no events, progress never moves backwards, and events from old runs never mix in.
- Duplicate and out-of-order events are ignored, and a stale cursor returns resync_required.
- SSE exposes neither the internal outbox nor unsanitized fields.
- Reconnect, Redis restart and terminal stop-reconnect behavior are covered by automated tests.

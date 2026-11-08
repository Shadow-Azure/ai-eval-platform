# Build the evaluation launch wizard and prepare/create flow

English | [中文](m5-04-eval-launch-wizard.md)

```yaml flow
kind: issue
milestone: m5
priority: P0
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
  number: 36
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/36
```

## Background

Launching an evaluation combines a dataset version, runtime, assets, model and resource pool, and freezes the configuration before creation. The prepare and create APIs and a usable wizard do not exist yet.

## Goals

- Implement prepare and create APIs that re-check capacity, credentials, refs and asset compatibility and return preparation_id, warnings and a snapshot summary.
- Build a four-step wizard: dataset, agent assets and runtime, model and resources, then summary confirmation.
- Use an idempotency key for creation and handle expired preparation, changed configuration, invalid capacity and forbidden credentials.
- Never persist temporary keys, and invalidate the previous preparation when any input changes.

## Acceptance

- A user creates a real evaluation from the page with a complete, traceable configuration snapshot.
- Submitting again with the same idempotency key returns the same evaluation, and 409 and 422 responses map to the relevant fields.
- Refreshing or leaving the page preserves non-sensitive drafts and never persists a temporary key.
- The wizard flow and visuals match the prototype, and the main path has tests.

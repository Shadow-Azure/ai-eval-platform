# Administrator operations status page

English | [中文](m3-06-admin-operations-status.md)

```yaml flow
kind: issue
milestone: m3
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m3-06-admin-operations-status.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 61
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/61
```

## Background

M3 now carries real datasets, the model gateway, the execution runtime, and account sessions, but the control plane only has an evaluation-oriented overview page. An administrator cannot confirm the current state of services, execution resources, and the database in one place. Operations needs a single view of this infrastructure so an incident can be traced to the layer that failed.

## Goals

- Add an administrator-only "operations status" sub-page: a read-only member does not see it in navigation, direct access shows an access-denied state, and the backend endpoint returns 403.
- Service status covers the control-plane API, PostgreSQL, Redis, object storage, the Runner plane, the dispatch worker, and the model gateway: configured checks report liveness, unconfigured items explicitly say "disabled", and failures show a readable reason.
- Resource status shows resource pools with Runner online counts and last heartbeat, evaluation counts, dataset and case counts, model configuration count, and dispatch queue backlog.
- Database status shows connectivity, migration revision, database size, and key table row counts without returning connection strings, keys, or any plaintext credentials.
- The dispatch worker writes a database heartbeat on every poll so operations status can judge its liveness; the page supports manual refresh and periodic auto-refresh.

## Acceptance

- An administrator opening `/operations` sees services, resources, and database status; a read-only member sees an access-denied state and `GET /api/v1/operations/status` returns 403, while an anonymous request returns 401.
- Service items cover the API, database, Redis, object storage, Runner, worker, and model gateway; disabled, failed, and healthy states are distinguishable on the page with a reason.
- The resource area shows Runner and resource pools, evaluations, datasets, model configurations, and dispatch queue counts; the database area shows migration revision, size, and key table row counts.
- pytest covers administrator success, read-only member 403, anonymous 401, service fields, fresh/stale worker heartbeat, and database table rows; frontend typecheck and build pass.
- The PR includes before/after screenshots at the same viewport and theme, where before is the real application state without the new page and after is the new operations status page.

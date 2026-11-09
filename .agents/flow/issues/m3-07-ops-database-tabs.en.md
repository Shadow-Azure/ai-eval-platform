# Operations status database tabs and capacity configuration

English | [中文](m3-07-ops-database-tabs.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m3-07-ops-database-tabs.*
  - backend/**
  - deploy/**
  - web/**
  - tests/**
  - docs/**
  - .github/workflows/deploy.yml
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 67
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/67
```

## Background

The first operations status page treats PostgreSQL as the only database block; Redis and object storage appear only as one-line status entries in the service list, so an administrator cannot switch between the two stores and inspect their capacity and usage in one place. The PostgreSQL storage limit currently shows "not configured" because the production database runs in a Docker volume on the ECS system disk: PostgreSQL exposes no disk capacity limit, and the deploy pipeline never passes a capacity value to the API.

## Goals

- Turn the database area into three clickable tabs — PostgreSQL, Redis, and object storage — each showing the corresponding service status, usage, and capacity information.
- Keep the PostgreSQL tab's migration revision, database size, connection watermark, storage watermark, and table rows; show Redis memory usage/limit, clients/limit, version, and probe latency; show the object-storage bucket, endpoint, probe latency, and status.
- Pass a new deployment secret `DATABASE_STORAGE_LIMIT_BYTES`: the deploy workflow writes it into the host `.env` and Compose hands it to the API; when unset the page explicitly says so instead of inventing a limit.
- Never return connection strings, access keys, or other plaintext credentials; a read-only member still sees no page entry and the API returns 403.

## Acceptance

- An administrator can switch between PostgreSQL, Redis, and object storage tabs on `/operations`; each tab shows the corresponding status and capacity information, with a readable reason when a service is unconfigured or a probe fails.
- The PostgreSQL tab shows connection and storage watermarks; the Redis tab shows used memory/limit and clients/limit; the object-storage tab shows the bucket, endpoint, and probe result.
- The deploy workflow writes `DATABASE_STORAGE_LIMIT_BYTES` into the host `.env` and passes it to the API; without the secret the page still renders and says "limit not configured".
- pytest covers the three database blocks' status and capacity fields, Redis disabled/healthy probes, and the object-storage probe; frontend typecheck and build pass.
- The PR includes before/after screenshots at the same viewport and theme, where before is the current single database block and after is the tabbed version.

# [m8-34] Zero-resident spot pool with per-job ephemeral instances

English | [中文](m8-34-spot-zero-resident.md)

```yaml flow
kind: issue
milestone: m8
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m8-34-spot-zero-resident.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/issues/m8-33-runtime-image-fallback.*
  - backend/app/config.py
  - backend/app/main.py
  - .github/workflows/deploy.yml
  - deploy/docker-compose.prod.yml
  - backend/app/api/**
  - backend/app/services/capacity_control.py
  - backend/app/services/provisioning/**
  - tests/backend/**
  - plans/2026-11-15-pr253-review.md
  - plans/m1-02-fullstack-design/06-execution-and-resource-pools.md
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
adr: []
github:
  number: 252
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/252
```

## Background

Spot instances stay online while ephemeral runners exit after one job. Capacity control waits `heartbeat_timeout + lost_grace` (~390s) and scans every 300s, so five concurrent shards starve. Not a price or image issue.

## Goal

- Zero resident: no warm pool, min capacity or idle runners; current_size=0 with no jobs.
- One instance per job: shut down/terminate immediately when the runner exits.
- Event-driven: GitHub `workflow_job` webhook triggers reconcile; 30s poll fallback.
- Cold start via pre-baked AMI/pre-pulled images, not resident capacity.

## Acceptance

- 0 instances with no jobs; 5 jobs create 5 instances; each instance terminates within 1 minute after its job.
- No long-lived `instance online / runner offline`.
- Cancel leaves no shards/instances; backend tests/CI pass.

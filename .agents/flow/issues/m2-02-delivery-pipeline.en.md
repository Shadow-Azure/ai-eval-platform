# Establish the Aliyun automated migration and deployment pipeline

English | [中文](m2-02-delivery-pipeline.md)

```yaml flow
kind: issue
milestone: m2
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m2-02-delivery-pipeline.*
  - .github/workflows/**
  - deploy/**
  - scripts/**
  - backend/migrations/**
  - tests/**
  - docker-compose.yml
adr: []
github:
  number: 12
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/12
```

## Background

The platform must deploy to Aliyun through the same pipeline from its first runnable version; otherwise database migrations, runner callbacks, and production health cannot be verified continuously.

## Goals

- Run documentation, flow, unit, contract, and migration dry-run gates during pull requests.
- After a main merge, build API, worker, and web images with immutable digests and push them to ACR.
- Run a singled-flight migration on RDS, update ECS Compose services, and execute health checks.
- Record deployment version, image digests, migration revision, and outcome while retaining a compatible application rollback path.

## Acceptance

- One main merge automatically completes tests, image builds, database migration, application deployment, and health checks.
- Empty-database migration failures and health-check failures block later steps and identify the failed stage in the deployment record.
- The previous compatible image can be restored through a controlled path without automatically running a destructive database downgrade.
- Deployment keys and cloud credentials do not appear in logs, images, repository files, or frontend artifacts.

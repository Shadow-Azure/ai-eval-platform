# Establish the control-plane and database foundation

English | [中文](m2-01-control-plane-foundation.md)

```yaml flow
kind: issue
milestone: m2
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m2-01-control-plane-foundation.*
  - backend/**
  - web/**
  - plans/**
  - reports/**
  - tests/**
  - pyproject.toml
  - package.json
  - package-lock.json
  - docker-compose.yml
adr: []
github:
  number: 11
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/11
```

## Background

The evaluation platform currently has architecture, prototype, and design documents but no running service capable of accepting execution callbacks and persisting state. M2 must first establish a minimal control plane and database foundation so deployment, runners, and execution protocols have a real target.

## Goals

- Establish an engineering skeleton with a modular FastAPI monolith, minimal web shell, SQLAlchemy, and Alembic.
- Create a default tenant/workspace/project and foundational tables for evaluations, cases, attempts, events, outbox, and resources.
- Provide a local Compose environment with PostgreSQL, Redis, and S3-compatible object storage.
- Add automated coverage for migrations, unique constraints, tenant scoping, and state persistence.

## Acceptance

- An empty database migrates to the latest revision in one run, with clear results for repeated and failed migrations.
- Local Compose starts API, worker, web, PostgreSQL, Redis, and object storage with health checks.
- Core tables include idempotency keys, tenant boundaries, and required indexes, and data survives a service restart.
- Automated tests cover migrations, composite constraints, default-tenant creation, and the minimal healthy API path.

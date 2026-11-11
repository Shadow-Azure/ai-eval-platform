# Standardize production secret injection and host migration

English | [中文](m7-deployment-secrets-migration.md)

```yaml flow
kind: issue
milestone: m7
priority: P1
status: done
scope:
  - AGENTS.md
  - .agents/flow/issues/m7-deployment-secrets-migration.*
  - .github/workflows/deploy.yml
  - deploy/**
  - tests/deploy/**
  - plans/**
  - reports/**
adr: []
github:
  number: 116
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/116
```

## Background

Long-lived production secrets (SECRET_BOX_KEY, POSTGRES_PASSWORD, AUTH_SESSION_SECRET, GATEWAY_TOKEN_SECRET, RUNNER_TASK_SECRET, RUNNER_HEARTBEAT_TOKEN, etc.) are currently generated on the host `.env` at first deploy or maintained manually. When moving to another host without migrating `.env`, the new keys make existing ciphertext undecryptable (model keys and cloud connections break), and the deployment is not reproducible.

## Goals

- Move these long-lived secrets into GitHub Secrets and have the Deploy workflow write them to the production host `.env`; the host `.env` keeps only values injected from Secrets and rebuildable deployment metadata.
- Inject SECRET_BOX_KEY, RUNNER_HEARTBEAT_TOKEN, RUNNER_ADMIN_TOKEN into the worker container and add RUNNER_ADMIN_TOKEN to the api container.
- Add a hard constraint to AGENTS.md: deployment must support host migration and long-lived secrets must come from GitHub Secrets.

## Acceptance

- GitHub Secrets contain SECRET_BOX_KEY, POSTGRES_PASSWORD, AUTH_SESSION_SECRET, GATEWAY_TOKEN_SECRET, RUNNER_TASK_SECRET, RUNNER_HEARTBEAT_TOKEN, RUNNER_ADMIN_TOKEN with the same values as the current production `.env`.
- The Deploy workflow writes these values to the host `.env` while preserving other keys; a new host can decrypt existing credentials with the same keys.
- `deploy/docker-compose.prod.yml` exposes the needed env to api/worker; existing deployment contract tests pass.
- AGENTS.md contains the host-migration and Secrets-injection constraint.

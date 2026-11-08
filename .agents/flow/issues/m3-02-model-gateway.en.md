# Manage model credentials and add the centralized model gateway

English | [中文](m3-02-model-gateway.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 24
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/24
```

## Background

Model keys must not reach runners or evaluation snapshots; runners call the platform gateway with short-lived credentials scoped to a case and attempt. The platform currently has no model configuration, credential encryption, connectivity check or forwarding implementation.

## Goals

- Add credential and model_config tables, store keys with envelope encryption, and support masking, rotation and revocation.
- Provide model configuration APIs and connectivity tests for OpenAI-compatible endpoints, keeping temporary configurations out of the shared directory.
- Implement the gateway route: verify the short-lived token, decrypt the upstream key, stream the response, record usage and enforce the budget.
- Normalize provider errors into stable codes and keep credentials out of request headers and logs.

## Acceptance

- Plaintext keys never appear in API responses, logs, evaluation snapshots or runner payloads.
- The gateway resolves tenant, model configuration and attempt from the token, and rejects unauthorized model names and configurations.
- Usage is written to the attempt after a streamed request, and exceeded budget returns budget_paused.
- Authentication failure, rate limiting, provider unavailability and budget rejection are covered by automated tests.

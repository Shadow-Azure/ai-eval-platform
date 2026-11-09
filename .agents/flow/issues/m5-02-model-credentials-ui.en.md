# Build model credential and configuration pages

English | [中文](m5-02-model-credentials-ui.md)

```yaml flow
kind: issue
milestone: m5
priority: P0
status: ready
scope:
  - .agents/flow/issues/m5-02-model-credentials-ui.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 84
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/84
```

## Background

The APIs for model credentials, model configuration, key encryption, masking, rotation, revocation, and connectivity testing are complete, but the Model Credentials navigation entry is still a placeholder. The main path therefore still requires seed data or manual API calls and cannot satisfy the M5 goal of removing dependence on seeds and the command line.

## Goals

- Build model configuration list and detail views showing provider, endpoint, model name, API protocol, pricing, credential mask, and recent usage.
- Build create and edit flows that bind an existing credential or create one in the same flow; keys are write-only and never enter URLs, local storage, or query caches.
- Add credential rotation, revocation, and model endpoint connectivity tests with stable field-level errors and diagnostics on failure.
- Preserve administrator write access and read-only member boundaries while reusing existing encryption, model-gateway, and audit capabilities.

## Acceptance

- An administrator can create a model configuration, test its endpoint, rotate a credential, and revoke it from the UI, while a regular member cannot perform writes.
- The UI and API never return plaintext keys, and browser refresh, navigation history, and query caches retain no secrets.
- The launch wizard immediately sees new usable model configurations after changes, while invalid or revoked credentials create explicit preflight blockers.
- Component or API integration tests cover the key form, permissions, empty and error states, rotation, and revocation.

# Define the generic runner task protocol and synthetic-task workflow

English | [中文](m2-03-runner-contract.md)

```yaml flow
kind: issue
milestone: m2
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m2-03-runner-contract.*
  - runner-contract/**
  - runner/**
  - tests/runner/**
  - tests/fixtures/runner/**
  - docs/architecture.*
  - plans/**
adr: []
github:
  number: 13
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/13
```

## Background

Evaluation execution must be decoupled from specific datasets, agents, and cloud resources. M2 freezes the replayable task reference, manifest, and callback protocol and validates it with a synthetic task that does not call a model; real evaluation arrives in M3.

## Goals

- Define the minimal dispatch contract carrying eval_run, shard, attempt, manifest URL, and digest.
- Define versioned schemas for task manifests, phase heartbeats, upload manifests, completion, and failure callbacks.
- Provide a reusable generic workflow template for the private runner repository and a synthetic-task execution script.
- Add contract tests for manifest validation, duplicate dispatch, object digests, successful callbacks, and retryable failures.

## Acceptance

- Repeated arrival of the same dispatch key does not start a second valid execution.
- A task is rejected before execution when the manifest URL, digest, attempt, or token does not match.
- The synthetic task fetches its manifest, produces a deterministic result, uploads objects, and submits a structured completion callback.
- Local fixtures and the real GitHub workflow use the same schema, with no dataset-specific or agent-specific branches.

# Upgrade the evaluation launch wizard and preflight checks

English | [中文](m5-04-eval-wizard-v2.md)

```yaml flow
kind: issue
milestone: m5
priority: P0
status: ready
scope:
  - .agents/flow/issues/m5-04-eval-wizard-v2.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 86
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/86
```

## Background

The current launch wizard supports a minimal flow for datasets, models, runtimes, and resource pools, but it lacks agent assets, Git refs, case subsets, concurrency, timeouts, retries, and budget inputs, and creation does not fully reuse a preflight snapshot. After M4 concurrency and budget semantics and the M5 configuration pages are available, these pieces must be composed into a verifiable product path.

## Goals

- Expand the wizard into a complete flow covering dataset and cases, agent assets and ref, model and resources, execution policy, and final confirmation.
- Support selecting a case subset or count, configuring concurrency, timeout, failure retries, and model budget, and showing capacity warnings plus an estimated cost range.
- Return an expiring preparation snapshot from the server that freezes dataset version, asset commit, runtime, model, resource pool, and execution policy; changed configuration invalidates direct creation from the old snapshot.
- Use idempotency keys for creation, map 409, 422, and preflight blockers to fields, and preserve non-sensitive drafts through refresh or navigation.

## Acceptance

- A user can select a complete evaluation composition and create a real evaluation without seeds or the command line, and the run records every frozen input.
- Repeating the same idempotency key returns the same evaluation, while configuration changes, expired preparation, and invalid capacity produce explicit UI messages.
- The backend revalidates concurrency, timeout, retry, and budget values instead of trusting frontend defaults, and temporary keys are never persisted in the browser.
- Tests or visual comparison evidence cover the key path, empty and error states, back-navigation edits, and primary actions in the target window sizes.

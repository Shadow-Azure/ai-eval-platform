# Build resource connection and resource pool pages

English | [中文](m5-03-resource-pools-ui.md)

```yaml flow
kind: issue
milestone: m5
priority: P0
status: ready
scope:
  - .agents/flow/issues/m5-03-resource-pools-ui.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 85
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/85
```

## Background

The operations page exposes resource pools and runners only as read-only data, while `/resources` remains a placeholder. M4 will add resource connections, pool configuration, and provisioning APIs, but users must be able to verify connections, create pools, inspect capacity, and understand queue reasons in the product without relying on the command line.

## Goals

- Build resource-connection list and creation flows showing provider, region, capabilities, verification state, last verification time, and masked credentials.
- Build resource-pool creation and editing so only provisioning modes compatible with the selected connection are available, with reasons shown before submission for incompatible choices.
- Show pool capacity bounds, online and busy runners, current price, threshold, latest heartbeat, next-check time, and queue reasons.
- Provide a provisioning-test action with a cost warning and confirmation, record an audit result, and handle empty, failure, and unauthorized states completely.

## Acceptance

- A user can create a resource connection and pool from the UI, and both API and UI reject incompatible capability and provisioning-mode combinations with explicit reasons.
- The resource-pool page distinguishes online, busy, offline, and starting runners and explains queueing caused by price, inventory, or capacity.
- Provisioning tests show an explicit cost warning and confirmation, display the transition from instance creation to runner registration on success, and retain diagnosable failures.
- The page never displays cloud secrets or full connection strings, administrator actions create audit records, and regular members see only information they are authorized to access.

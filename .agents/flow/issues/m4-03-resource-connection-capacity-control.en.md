# Add resource connections, pool configuration, and capacity control

English | [中文](m4-03-resource-connection-capacity-control.md)

```yaml flow
kind: issue
milestone: m4
priority: P0
status: done
scope:
  - .agents/flow/issues/m4-03-resource-connection-capacity-control.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - tests/**
  - docs/**
  - plans/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 80
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/80
```

## Background

Resource pools currently contain only a provider key, labels, and status, which is enough for fixed runners but cannot express cloud connections, provider capabilities, provisioning modes, capacity bounds, or price policy. Although a `ProvisionedInstance` skeleton exists, there is no replaceable provisioner interface, capacity controller, or compatibility validation, so M5 cannot safely build resource-pool pages.

## Goals

- Add tenant-level resource connections backed by encrypted credentials with `kind=resource`, recording provider, region, capabilities, verification state, and last verification time.
- Extend resource-pool configuration with constrained relationships among connection, provisioning mode, minimum and maximum capacity, instance parameters, price threshold, and labels.
- Define a common provisioner interface for connection capability validation, creation, release, and health queries, with a fake provider for repeatable capacity-control tests.
- Compute capacity gaps from queued cases, idle runners, and starting instances, then publish scale-up, scale-down, and price-wait reasons for the scheduler and operations UI.

## Acceptance

- Creation rejects incompatible connection and provisioning-mode combinations with stable error codes before creating a pool or instance.
- The fake provider scales to the capacity gap, unregisters runners before releasing idle instances, and covers creation failure, insufficient inventory, and release failure.
- Tasks remain queued when the current price exceeds the threshold, and pool state plus events expose current price, threshold, checked time, and next-check time.
- Connection secrets never appear in API responses, logs, events, or runner-accessible data, and migration plus capability-validation tests pass.

# Implement agent asset sources, immutable snapshots, and runtime injection

English | [中文](m5-01-agent-assets-snapshot.md)

```yaml flow
kind: issue
milestone: m5
priority: P0
status: ready
scope:
  - .agents/flow/issues/m5-01-agent-assets-snapshot.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - runner/**
  - tests/**
  - docs/**
  - package.json
  - package-lock.json
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 83
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/83
```

## Background

The target evaluation composition is "runtime x agent asset source x model x resource pool", but `/assets` is still a placeholder, evaluation creation only carries an empty `asset_snapshot_json`, and the runner does not consume asset snapshots. Users cannot configure skills, commands, or subagents in the UI, so real evaluations can use only runtime built-ins.

## Goals

- Add agent asset sources, immutable snapshots, and asset records that store Git source, credential, default ref, scan path, selection mode, and asset types.
- Implement ref resolution, directory scanning, and asset detection; skills are identified by directories containing `SKILL.md`, while commands and subagents follow native runtime formats and require explicit selection when detection is unreliable.
- Freeze selected assets into an immutable OSS bundle with resolved commit, tree digest, file digests, and version, and share one snapshot across every case in an evaluation.
- Consume snapshots in the runner, map assets into `.claude/`, and build asset-source management, asset-tree selection, compatibility guidance, and snapshot detail pages.

## Acceptance

- A user can create an asset source, resolve a ref, scan assets, and select either the full directory or explicit paths from the UI, with identical results after refresh or reopening.
- Changing a branch or deleting files after launch does not affect a started evaluation, and the evaluation records the requested_ref, resolved_commit, and file digests for traceability.
- Claude Code loads only selected skills, commands, and subagents from the snapshot, does not consume unrelated host assets, and presents explicit degradation messages for unsupported asset types.
- Repeated resolution and packaging are idempotent, credentials and asset contents never enter logs, and backend, runner, frontend, and asset golden-fixture tests pass.

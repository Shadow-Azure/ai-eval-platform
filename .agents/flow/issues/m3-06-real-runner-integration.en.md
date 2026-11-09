# Wire real Claude Code runtime and official SWE-bench scoring into eval shards

English | [中文](m3-06-real-runner-integration.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: ready
scope:
  - .agents/flow/issues/m3-06-real-runner-integration.*
  - .agents/flow/issues/m3-06-admin-operations-status.*
  - runner/**
  - .github/workflows/eval-shard.yml
  - backend/**
  - tests/**
  - reports/**
adr: []
github:
  number: 63
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/63
```

## Background

M2-05 proved dispatch/token/upload/callback with synthetic-task; the M3-04 real E2E ran Claude Code and the official harness manually in a temporary environment and uploaded the report. The production `eval-shard` still calls synthetic-task for every task. Production eval `a80b3802` is therefore succeeded/not_scored: Claude Code and official SWE-bench scoring are not on the normal runner path.

## Goals

- `eval-shard` selects the real runtime for dataset tasks, runs Claude Code from `task_json`, injects a short-lived model token, and captures patch/trace.
- Apply the test patch inside the isolated execution image, run official F2P/P2P, and produce an official report keyed by `instance_id`.
- Upload score/trace through the existing presigned flow; after completion the results API shows resolved/not_scored with test-level detail.
- Keep the synthetic path and M2 regressions; model keys, long-lived credentials, and private content never enter logs or objects.

## Acceptance

- A production UI evaluation of astropy__astropy-12907 runs automatically on `fixed-linux-eval` and resolves; the results API returns 100%, 2 F2P + 13 P2P, with no manual report upload.
- Synthetic evaluations still resolve, and runner-contract plus M2 regression tests stay green.
- Failure, timeout, and empty patch map to not_scored/unresolved with diagnosable errors.
- Logs and objects contain no model key; short-lived tokens bind to eval_run/shard/attempt.
- Backend and runner CI stay green, with reports and screenshots archived under `reports/`.

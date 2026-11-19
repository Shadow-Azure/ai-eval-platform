# [m8-45] Replace the platform queue timeout with a shard progress lease and stop the fixed 3600-second false kills

English | [中文](m8-45-queue-progress-lease.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/**
  - backend/**
  - plans/**
  - tests/**
adr: []
github:
  number: 307
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/307
```

## Background

When a shard is dispatched the platform marks every attempt in it as `queued`; the reaper then measures a fixed `eval_queue_timeout_seconds` (3600 seconds by default) from that same instant and `queue_timeout` is not retryable. A shard often contains several waves of Jobs: with `shard_size=64`, effective concurrency 8 and 60-minute cases, only the first wave finishes within the first hour and roughly 48 attempts that never `started` are killed; with concurrency 1 it is roughly 62. The defect is in the platform attempt state machine, not in the GitHub job `timeout-minutes`.

## Goals

- Replace the fixed queue budget with a shard-level progress lease: the initial budget is `case_timeout + 600s`, and whenever any attempt in the shard `started`, or a started attempt reaches a terminal state, the still-waiting siblings are renewed.
- Only a whole shard with no progress for longer than the budget is settled as `queue_timeout`; the absolute lease ceiling is 23 hours (82800 seconds), below GitHub's 24-hour self-hosted job queue limit.
- Heartbeats do not renew: a heartbeat proves the process is alive, not that the shard is advancing.
- Queued attempts that predate the upgrade (no lease columns) fall back to the old rule without failing the migration; the `queue_timeout` event records the queue wait and progress snapshot so the detail page can distinguish a queue timeout from an execution timeout and show the queue wait.

## Plan

- Keep `eval_queue_timeout_seconds` as the legacy-row fallback and add `eval_queue_timeout_grace_seconds=600` and `eval_queue_timeout_max_seconds=82800`.
- Add nullable `queue_started_at` and `queue_deadline` columns to `eval_case_attempt`; the dispatcher stamps the initial lease when the shard is really dispatched.
- Batch-renew the still-waiting attempts of the same shard on `started` and on terminal states of started attempts; the reaper reads only `queue_deadline` and falls back to `phase_changed_at + eval_queue_timeout_seconds` when it is null.
- Add `queue_wait_seconds`, `last_progress_at`, `queue_deadline`, `shard_size` and `concurrency` to the `case.timeout` event, and state the actual wait in the error summary.

## Acceptance

- With `shard_size=64`, concurrency 8 and 60-minute cases, the roughly 48 `queue_timeout` attempts no longer appear at the one-hour mark; every wave start/end renews the lease until all cases finish.
- With `shard_size=64`, concurrency 8 and 30-minute cases, no false kills either; the later attempts keep waiting and eventually start.
- A shard with no attempt `started` for 70 continuous minutes (`case_timeout=3600 + 600`) still settles as `queue_timeout`.
- While one attempt in a shard runs and the rest wait, the waiting attempts are not killed at 60 minutes; the running attempt still obeys `case_timeout` / `heartbeat_timeout`.
- Once the lease reaches the 23-hour ceiling, renewal stops and the attempt times out; the lease never extends forever.
- A `queued` attempt that predates the upgrade (no `queue_deadline`) falls back to the old 3600-second rule and the migration does not fail.
- New lease/renewal/fallback/ceiling tests under `tests/backend` pass; `npm test`, `npm run verify:flow` and `npm run verify:docs` pass.

## Scope

- Involves: `backend/app/config.py`, `backend/app/models/evaluation.py`, `backend/migrations/versions/`, `backend/app/services/dispatcher.py`, `backend/app/services/case_attempts.py`, `backend/app/services/lifecycle.py`, `backend/app/services/reaper.py`, `tests/backend/`.
- Does not involve: GitHub execute job `timeout-minutes` (tracked by #304), spot pool reliability and capacity accounting (tracked by #306), the `shard_size` default itself, or a new execution backend or queue system.

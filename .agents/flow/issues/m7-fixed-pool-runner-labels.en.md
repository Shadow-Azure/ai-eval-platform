# Fix fixed-pool label resolution that leaves evaluation shards queued forever

English | [中文](m7-fixed-pool-runner-labels.md)

```yaml flow
kind: issue
milestone: m7
priority: P1
status: ready
scope:
  - backend/app/services/runner_labels.py
  - backend/app/services/runners.py
  - tests/backend/test_runner_labels.py
  - tests/backend/test_evaluations.py
  - tests/backend/test_runners.py
  - .agents/flow/issues/m7-fixed-pool-runner-labels.*
adr: []
github:
  number: 136
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/136
```

## Background

At 15:25 on 2026-11-11 (Asia/Shanghai) the user started evaluation 707bb3bc on the fixed-linux system pool, and its execute job never left the queue. Investigation showed the system fixed pool stores `{"kind": "fixed"}` in labels_json, while shard dispatch treats the keys of labels_json as Runner labels; the resulting runs_on was `["kind"]`. The real Runner only registers self-hosted/linux/eval, so the job could never be picked up. Every evaluation that selects the fixed system pool is affected, and the run fails once the queue timeout expires.

## Goals

- Treat `{"kind": "fixed"}` as a legacy provider marker, not a Runner label; fixed pools without explicit runs_on/labels fall back to self-hosted, linux, eval.
- Make ensure_fixed_pool write explicit runs_on when it creates the system pool, so the placeholder is no longer produced.
- Add regression tests for shard manifest/dispatch labels and fixed-pool creation.

## Acceptance

- A fixed pool whose labels_json is `{"kind": "fixed"}` dispatches with runs_on `["self-hosted", "linux", "eval"]`.
- An explicit runs_on or labels configuration on a fixed pool still wins.
- A pool created by ensure_fixed_pool stores an explicit runs_on in labels_json.
- After deployment a newly launched evaluation lands on the real online fixed Runner; the timed-out 707bb3bc must be relaunched.
- The relevant pytest, flow and documentation gates pass.

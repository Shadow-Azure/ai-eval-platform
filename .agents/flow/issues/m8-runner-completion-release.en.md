# [m8] Release the Runner binding after a successful completion

English | [中文](m8-runner-completion-release.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-runner-completion-release.*
  - backend/app/services/case_attempts.py
  - tests/backend/test_case_callbacks.py
  - tests/backend/test_reconciler.py
adr: []
github:
  number: 281
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/281
```

## Background

Reproduced on the production fixed pool on 2026-11-18: after a case completed successfully (resolved), `runner.busy_case_attempt_id` still pointed at that case, so the next fixed evaluation's `started` callback was rejected with 409 `runner_busy_conflict`; the Runner recorded `stale_attempt` and exited, leaving the evaluation queued until it was cancelled and the binding was released manually. The failure, timeout and spot-interruption paths release the binding through `lifecycle.release_runner_binding`; the successful-completion path is settled by `finalize_completion` but never releases it, and the Runner's terminal callbacks do not carry `X-Runner-Id`, so the API-edge `release_case` does not fire either.

## Goals

- Release the Runner bound to the attempt inside the `finalize_completion` transaction, covering both Runner callbacks and reconciler backfilled completions.
- Keep no-op behavior when there is no binding, no `runner_id`, or the binding belongs to another case; preserve the existing failure/spot/budget-pause release semantics.
- Add regression tests for started-then-completed release and for the real callback shape without `X-Runner-Id`.

## Acceptance

- After `started` binds a Runner, a `completed` callback without `X-Runner-Id` clears `busy_case_attempt_id` and returns the Runner to `online`.
- A reconciler backfilled completion releases the binding too; completing an unbound case has no side effect.
- Existing case callback, lifecycle and reconciler tests plus CI stay green.

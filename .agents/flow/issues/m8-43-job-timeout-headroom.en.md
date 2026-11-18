# [m8] Fix execute job timeout mismatch with Agent/case budgets

English | [中文](m8-43-job-timeout-headroom.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-43-job-timeout-headroom.*
  - .github/workflows/eval-shard.yml
  - backend/app/agent_timeout.py
  - backend/app/config.py
  - backend/app/services/case_attempts.py
  - backend/app/services/dispatcher.py
  - backend/app/services/evaluations.py
  - backend/app/services/lifecycle.py
  - backend/app/services/preparations.py
  - docs/architecture.*
  - runner-contract/schemas/v1/dispatch.schema.json
  - runner-contract/schemas/v1/failure.schema.json
  - runner/lib/agent-exec.mjs
  - runner/lib/cancellation.mjs
  - runner/scripts/real-task.mjs
  - runner/workflows/eval-shard.yml
  - scripts/doc-budgets.manifest.json
  - tests/backend/test_execution_timeout_hierarchy.py
  - tests/backend/test_failure_classification.py
  - tests/backend/test_preparations.py
  - tests/eval-shard-workflow.test.mjs
  - tests/runner/agent-exec.test.mjs
  - tests/runner/agent-timeout.test.mjs
  - tests/runner/cancellation.test.mjs
  - tests/runner/contracts.test.mjs
adr: []
github:
  number: 304
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/304
```

## Background

An evaluation froze `agent_timeout_seconds=3600` and `case_timeout_seconds=4200`, but the `execute` job hard-coded `timeout-minutes: 60`. The job starts before the Agent, so GitHub killed it at 60 minutes before the runner could upload a trace, score the case or send a terminal callback; the platform settled it as `heartbeat_timeout` after 300 seconds and retried the whole case with no output again.

## Goals

- Enforce `dispatch TTL > job timeout > case timeout > agent timeout`; raise an equal or shorter case automatically and reject an Agent budget that cannot leave headroom.
- Read the derived `job_timeout_minutes` from the dispatch payload in the `execute` job instead of a fixed 60 minutes.
- On SIGTERM/SIGINT, stop the Agent, upload a partial trace and send a terminal `runner_lost` failure; that failure must not trigger a full retry.
- Enqueue a successfully uploaded partial trace for parsing; a terminal failure without any trace is explicitly `unlinked`.
- Shorten the cancellation grace period to 60 seconds so the platform does not wait ten minutes after the runner has already reported.

## Acceptance

- For a 60-minute Agent, the job timeout is strictly greater than the case and the case strictly greater than the Agent; changing the Agent budget changes the job timeout.
- A cancelled runner reports `runner_lost`/`execution_timeout` and is not automatically rerun; an uploaded trace ends `incomplete`, otherwise `unlinked`.
- Unit tests cover the hierarchy, cancellation callback and retry classification; `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs` and the runner contract tests pass.
- Documentation records the hierarchy, default headroom, dynamic job timeout and 60-second cancellation grace.

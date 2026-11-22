# Propagate evaluation failure reasons to the console

English | [中文](m8-eval-failure-reason.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - backend/app/api/model_gateway.py
  - backend/app/model_gateway/service.py
  - backend/app/models/evaluation.py
  - backend/app/scoring/service.py
  - backend/app/scoring/swebench.py
  - backend/app/services/case_attempts.py
  - backend/app/services/evaluations.py
  - backend/app/services/lifecycle.py
  - backend/app/services/self_evolution.py
  - backend/app/services/workbench.py
  - backend/migrations/versions/0031_failure_detail.py
  - runner-contract/README.md
  - runner-contract/schemas/v1/completion.schema.json
  - runner-contract/schemas/v1/failure.schema.json
  - runner/lib/agent-exec.mjs
  - runner/lib/failure-classify.mjs
  - runner/scripts/real-task.mjs
  - tests/backend/test_case_callbacks.py
  - tests/backend/test_deployment_migration.py
  - tests/backend/test_eval_workbench.py
  - tests/backend/test_model_gateway.py
  - tests/backend/test_operations.py
  - tests/backend/test_self_evolution_report.py
  - tests/failure-view.test.mjs
  - tests/runner/contracts.test.mjs
  - tests/runner/failure-classify.test.mjs
  - web/src/api/evalWorkbench.ts
  - web/src/api/evaluations.ts
  - web/src/components/CaseBrowser.tsx
  - web/src/pages/EvalDetailPage.tsx
  - web/src/styles/eval-detail.css
  - web/src/utils/failure.ts
  - plans/2026-11-22-pr376-review.md
  - .agents/flow/issues/m8-eval-failure-reason.md
  - .agents/flow/issues/m8-eval-failure-reason.en.md
  - .agents/flow/issues/m8-eval-failure-reason.i18n.yaml
adr: []
github:
  number: 375
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/375
```

## Background

In production evaluations the real cause of an Agent/model failure never reaches the console case error or the bad-case report; only a node code such as `agent_failed` or `asset_source_install_failed` survives. Provider 429/usage-cap exhaustion, authentication failure, timeout, Agent crash and asset install failure cannot be told apart, so diagnosis requires manually pulling job logs and traces.

## Goals

- The Runner parses the Claude Code terminal error (429/401/timeout/exit) and writes a structured `failure` into the score report and completion callback: `code`, upstream text, `phase`, `request_id`, `http_status` and `retryable`.
- The gateway keeps the upstream request id and upstream text in 429/5xx error responses.
- The backend parses and persists the failure detail while staying compatible with old reports that only carry `error`/`message`; a 429 rate limit enters the existing attempt budget, provider usage-cap exhaustion stays permanent.
- The console case list and detail show the failure category, upstream text, request id and a trace entry; the evaluation detail aggregates the failure distribution; the bad-case `reason` uses the same structured cause.

## Acceptance

- 429/usage-cap, authentication, timeout, Agent crash and asset install failures show their structured category and upstream text in the case detail and link to the trace.
- The evaluation detail shows the failure reason distribution; the bad-case `reason` contains the upstream text.
- A 429 case enters the existing attempt retry budget; usage-cap exhaustion is marked permanent.
- Runner classification, parser message propagation, the frontend failure display model and callback persistence are covered by tests; `npm test`, `pytest tests/backend` and `npm run verify:flow` pass.

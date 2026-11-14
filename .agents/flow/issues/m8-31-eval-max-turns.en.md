# [m8-31] Optional max turns when starting an evaluation (default 200, console editable)

English | [中文](m8-31-eval-max-turns.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-31-eval-max-turns.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - prototype/app.js
  - prototype/index.html
  - backend/app/api/evaluations.py
  - backend/app/api/runners.py
  - backend/app/models/evaluation.py
  - backend/app/services/evaluations.py
  - backend/app/services/preparations.py
  - backend/migrations/versions/0026_eval_run_max_turns.py
  - runner-contract/schemas/v1/task-manifest.schema.json
  - runner/lib/task-payload.mjs
  - runner/scripts/real-task.mjs
  - tests/backend/test_evaluations.py
  - tests/backend/test_migrations.py
  - tests/backend/test_preparations.py
  - tests/backend/test_runners.py
  - tests/backend/test_deployment_migration.py
  - tests/backend/test_operations.py
  - tests/runner/task-payload.test.mjs
  - web/src/pages/EvalWizardPage.tsx
  - web/src/api/evaluations.ts
  - plans/m1-02-fullstack-design/05-data-model.md
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
adr: []
github:
  number: 246
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/246
```

## Background

`--max-turns` is currently a Runner-level default; complex command flows (design -> propose -> apply with a subagent) can exhaust it early. It should be optional when starting an evaluation.

## Goal

- Add an optional “max turns” field to wizard step 3; empty means 200, editable; range 1-1000.
- Step 4 summary shows the value; a turn is one model response (may contain multiple tool calls).
- Follow-up implementation: persist in the eval snapshot/API, pass to Runner, show on task detail.

## This round

- Prototype only: `prototype/app.js` + rebuilt `prototype/index.html`.
- Before/After screenshots sent on Feishu and added to issue #246.

## Acceptance

- Prototype step 3 shows the optional input/default hint; step 4 shows the effective value.
- Full implementation is a separate change (API/backend/Runner/tests/docs).

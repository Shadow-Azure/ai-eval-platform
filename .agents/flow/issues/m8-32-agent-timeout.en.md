# [m8-32] Raise the Agent timeout to 30 minutes

English | [中文](m8-32-agent-timeout.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-32-agent-timeout.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - runner/scripts/real-task.mjs
  - tests/runner/agent-timeout.test.mjs
  - prototype/app.js
  - prototype/index.html
  - plans/2026-11-15-pr249-review.md
  - backend/app/services/case_attempts.py
  - tests/backend/test_eval_workbench.py
  - backend/app/agent_timeout.py
  - backend/app/api/datasets.py
  - backend/app/api/evaluations.py
  - backend/app/api/runners.py
  - backend/app/models/dataset.py
  - backend/app/models/evaluation.py
  - backend/app/services/dataset_imports.py
  - backend/app/services/datasets.py
  - backend/app/services/evaluations.py
  - backend/app/services/preparations.py
  - backend/app/services/workbench.py
  - backend/migrations/versions/0027_agent_timeout.py
  - runner-contract/schemas/v1/task-manifest.schema.json
  - runner/lib/task-payload.mjs
  - runner/scripts/real-task.mjs
  - tests/backend/test_dataset_apis.py
  - tests/backend/test_dataset_import_upload.py
  - tests/backend/test_deployment_migration.py
  - tests/backend/test_evaluations.py
  - tests/backend/test_migrations.py
  - tests/backend/test_operations.py
  - tests/backend/test_preparations.py
  - tests/backend/test_runners.py
  - tests/runner/agent-timeout.test.mjs
  - tests/runner/task-payload.test.mjs
  - web/src/api/datasets.ts
  - web/src/api/evalWorkbench.ts
  - web/src/api/evaluations.ts
  - web/src/pages/DatasetDetailPage.tsx
  - web/src/pages/DatasetImportPage.tsx
  - web/src/pages/EvalDetailPage.tsx
  - web/src/pages/EvalWizardPage.tsx
  - plans/m1-02-fullstack-design/05-data-model.md
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
adr: []
github:
  number: 248
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/248
```

## Background

OpenSpec retest `ccbdb2f0` dispatched subagents but hit the 900s (15 min) wall-clock timeout; official tests never ran and the score was `agent_failed`.

## Goal

- Dataset versions store a default `agent_timeout_seconds` (1800s default, 1–1440 min), configurable at import.
- Evaluation launch may override it: evaluation override > dataset default > 1800s; frozen on the run.
- Runner uses the frozen effective value; `REAL_TASK_TIMEOUT_MS` remains the operator override.
- Env overrides remain; lock with a runner test.

## Acceptance

- Dataset default and launch override work; tests pass; post-deploy retest completes final verification.

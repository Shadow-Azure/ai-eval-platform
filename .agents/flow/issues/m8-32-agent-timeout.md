# [m8-32] Agent 超时提高到 30 分钟

[English](m8-32-agent-timeout.en.md) | 中文

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

## 背景

OpenSpec 复测 `ccbdb2f0` 已跑通 subagent，但 Agent 在 900s（15 分钟）wall-clock 超时，official test 未执行，score 为 `agent_failed`。

## 目标

- 数据集版本保存 `agent_timeout_seconds` 默认值（默认 1800s，1–1440 分钟）；导入时可配置。
- 评测启动可选覆盖 `agent_timeout_seconds`，生效优先级：评测覆盖 > 数据集默认 > 1800s；冻结到评测运行。
- Runner 使用冻结的生效值；`REAL_TASK_TIMEOUT_MS` 保留运维覆盖。
- 环境变量仍可覆盖；runner 测试锁定。

## 验收

- 数据集默认可配、评测启动可覆盖；测试通过；部署后复测能完成 final verification。

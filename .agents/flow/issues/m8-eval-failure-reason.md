# 评测失败原因透传与前端展示

[English](m8-eval-failure-reason.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
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

## 背景

生产评测中，Agent/模型失败的真实原因没有出现在控制台用例错误和 bad case 上报里，只留下 `agent_failed`、`asset_source_install_failed` 等节点码。供应商 429/配额耗尽、鉴权失败、超时、Agent 崩溃和资产安装失败无法区分，排查需要人工拉取 job 日志与轨迹。

## 目标

- Runner 解析 Claude Code 终态错误（429/401/timeout/exit），在评分报告与完成回调中写入结构化 `failure`：`code`、上游原文、`phase`、`request_id`、`http_status` 与 `retryable`。
- 网关在 429/5xx 错误响应中保留上游 request_id 与上游原文。
- 后端解析并持久化失败详情，兼容只写 `error`/`message` 的旧评分报告；429 限流按既有 attempt 预算重试，配额耗尽标记为永久失败。
- 控制台用例列表与详情显示失败类别、上游原文、request_id 与轨迹入口；评测详情汇总失败原因分布；bad case `reason` 使用同一结构化原因。

## 验收

- 429/配额、鉴权、超时、Agent 崩溃、资产安装失败在用例详情显示结构化类别与上游原文，并可跳转轨迹。
- 评测详情可看到失败原因分布；bad case `reason` 含上游原文。
- 429 用例按可重试错误进入既有 attempt 重试预算；配额耗尽标记为永久失败。
- Runner 分类、解析器 message 透传、前端失败展示模型与回调持久化均有测试；`npm test`、`pytest tests/backend`、`npm run verify:flow` 通过。

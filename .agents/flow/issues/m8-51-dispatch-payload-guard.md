# [m8-51] repository_dispatch 载荷传输层护栏与顶层字段收敛

[English](m8-51-dispatch-payload-guard.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-51-dispatch-payload-guard.*
  - .github/workflows/eval-shard.yml
  - backend/app/api/runners.py
  - backend/app/runner_contracts.py
  - backend/app/services/case_attempts.py
  - backend/app/services/dispatcher.py
  - backend/app/services/evaluations.py
  - backend/app/services/github_dispatch.py
  - backend/app/services/spot_fallback.py
  - docs/architecture.*
  - plans/2026-11-19-pr*-review.md
  - plans/m1-02-fullstack-design/**
  - runner/**
  - runner-contract/**
  - tests/**
adr: []
github:
  number: 323
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/323
```

## 背景

生产评测在派发阶段被 GitHub 以 422 拒绝：`client_payload` 最多允许 10 个顶层属性，而 #312 为镜像缓存加速把 `scripts_bundle` 复制进载荷后变成 11 个。派发没有创建 workflow run，Worker 把 422 当成可重试错误耗尽退避预算后进入 `dispatch_failed`，用例从未 `started`，bad case 上报也无法兜底。

## 目标

- 发 POST 前本地校验 GitHub 的载荷限制，违反时快速失败且错误可诊断，不调用 transport、不消耗重试预算。
- 采用方案 C：`client_payload` 只保留启动前必需的 8 个顶层字段，digest/expiry/脚本包 pin 由服务端 descriptor 提供，同时保留 workflow 获取脚本包 pin 的能力。
- 区分 422：参数校验失败为终态，spam/限流仍退避重试。
- 补回归测试与文档，契约 schema 版本化并固化 10 个顶层属性上限。

## 方案

- `github_dispatch` 增加 `DispatchPayloadInvalid` 预检：`client_payload` 必须是 JSON object、最多 10 个顶层属性、序列化不超过 60KB（GitHub 硬上限 64KB），`event_type` 不超过 100 字符；错误信息包含实际字段数或字节数。
- dispatcher 捕获预检错误后直接 `_exhaust_dispatch`，写入 `dispatch_payload_invalid`，用例转 `dispatch_failed`，不调用 transport。
- 422 响应体不含 `spammed` 时标记为不可重试并终态失败；含 `spammed` 的 422 与 429、5xx、网络错误维持退避重试。
- 新建与重试派发使用 schema 1.1 的 8 个字段：`schema_version`、`eval_run_id`、`shard_id`、`attempt`、`dispatch_key`、`manifest_url`、`runs_on`、`job_timeout_minutes`；digest 与 expires 存 outbox 行，由 manifest 响应的 `X-Manifest-Digest` / `X-Manifest-Expires-At` 返回。
- `scripts_bundle` pin 只留在 outbox 行，workflow 经分片 `/scripts` 元数据端点取回；schema 1.0 旧格式仍被接受，滚动发布期间的在途派发保持可用。
- `dispatch.schema.json` 用 `schema_version` 的 `oneOf` 区分 1.0/1.1，保留 `maxProperties: 10`；Python 与 Node 校验器共享同一分支语义。

## 验收

- 完整 bundle 配置下 schema 1.1 `client_payload` 为 8 个顶层键且序列化小于 60KB，dispatch 成功；`scripts_bundle`、`manifest_digest`、`expires_at` 在 outbox 行，manifest 响应头与 `/scripts` 端点可读取。
- 11 键、超 60KB、超 100 字符 `event_type` 的 payload 在本地被拒绝，transport 未被调用，错误包含具体字段数或字节数。
- 1.0 旧 dispatch 与 1.1 新 dispatch 都通过契约校验；1.1 缺少 `runs_on`/`job_timeout_minutes` 被拒绝。
- 422 参数错误不重试；422 spam、429、5xx 仍重试，均有单测覆盖。
- schema 拒绝 11 个顶层属性；Python 与 Node 校验器对 `oneOf`、`maxProperties` 行为一致。
- `pytest tests/backend`、`npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。

## 范围

- 涉及：`backend/app/api/runners.py`、`backend/app/services/{dispatcher,evaluations,case_attempts,spot_fallback,github_dispatch}.py`、`backend/app/runner_contracts.py`、`runner-contract/`、`runner/lib/`、`runner/scripts/`、`.github/workflows/eval-shard.yml`、`tests/backend/`、`tests/runner/`、`tests/integration/`、`docs/architecture.*`、`plans/m1-02-fullstack-design/`。
- 不涉及：排队超时与 6 用例调度（#307/#315）、模型/数据集/Runner 执行逻辑、bad case 上报本体、`workflow_dispatch.inputs`。

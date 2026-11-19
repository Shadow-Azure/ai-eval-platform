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
  - runner-contract/schemas/v1/dispatch.schema.json
  - runner/README.md
  - runner/lib/json-schema-subset.mjs
  - runner/scripts/hydrate-dispatch.mjs
  - scripts/doc-budgets.manifest.json
  - tests/**
adr: []
github:
  number: 323
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/323
```

## 背景

生产评测在派发阶段被 GitHub 以 422 拒绝：`client_payload` 最多允许 10 个顶层属性，而 #312 为镜像缓存加速把 `scripts_bundle` 复制进载荷后变成 11 个。派发没有创建 workflow run，Worker 把 422 当成可重试错误耗尽退避预算后进入 `dispatch_failed`，用例从未 `started`，bad case 上报也无法兜底。

## 目标

- 发 POST 前本地校验 GitHub 的载荷限制，违反时快速失败且错误可诊断，不调用 transport、不进入退避重试。
- 1.1 派发只保留 8 个启动前字段；`manifest_digest`、`expires_at`、`scripts_bundle` 由服务端 descriptor 在启动后取回，workflow 再交给 Runner。
- 区分 422：参数校验失败为终态，spam/限流仍退避重试。
- 补回归测试与文档，契约 schema 同时兼容 1.0 与 1.1。

## 方案

- `github_dispatch` 增加 `DispatchPayloadInvalid` 预检：`client_payload` 必须是 JSON object、最多 10 个顶层属性、序列化不超过 60KB（GitHub 硬上限 64KB），`event_type` 不超过 100 字符；错误信息包含实际字段数或字节数。
- dispatcher 捕获预检错误后直接 `_exhaust_dispatch`，写入 `dispatch_payload_invalid`，用例转 `dispatch_failed`，不调用 transport；旧的 11 键行会先删除载荷中的 `scripts_bundle` 副本并保留 outbox pin。
- 422 只对已知的参数校验消息终态失败；含 `spammed` 的 422、未知 422、429、5xx、网络错误维持退避重试。
- 方案 C：1.1 派发只带 `schema_version`、`eval_run_id`、`shard_id`、`attempt`、`dispatch_key`、`runs_on`、`job_timeout_minutes`、`manifest_url`；新增分片 descriptor 端点，用 manifest 签发的短期 token 验签、按分片限定并限流，返回 digest、expires 与 scripts bundle pin。
- workflow 在 prepare 拉起后 hydrate descriptor，把 digest/expiry 合并进 Runner 使用的 dispatch 文件并传给 execute；scripts bundle 仍由 `/scripts` 端点提供 pin。1.0 旧派发原样透传。
- `dispatch.schema.json` 用 `oneOf` 同时接受 1.0（10 键）与 1.1（8 个启动字段）；Python 与 Node 的 schema 子集补 `maxProperties`/`minProperties`。

## 验收

- 完整 bundle 配置下 1.1 `client_payload` 为 8 个顶层键且序列化不超过 60KB，dispatch 成功；`scripts_bundle`、`manifest_digest`、`expires_at` 保留在 outbox 行。
- descriptor 在有效短期 token 下返回 digest/expires/bundle pin；无 token/错误 token 返回 401，跨分片 token 被拒绝，超限返回 429。
- workflow hydrate 后用 descriptor 的 digest 校验 manifest 字节并合并 expires；1.0 旧 dispatch 不调用 descriptor、原样透传。
- 11 键、超 60KB、超 100 字符 `event_type` 的 payload 在本地被拒绝，transport 未被调用；旧代码留下的 11 键行会先删除载荷副本并成功派发。
- 422 已知参数错误不重试；422 spam、未知 422、429、5xx 仍重试，均有单测覆盖。
- 无载荷 pin 时 workflow 仍能用 `/scripts` 元数据校验并使用预置镜像缓存；缓存缺失才下载包。
- schema 同时接受 1.0 10 键与 1.1 8 键、拒绝 11 键；Python 与 Node 校验器行为一致。
- `pytest tests/backend`、`npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。

## 范围

- 涉及：`.github/workflows/eval-shard.yml`、`backend/app/api/runners.py`、`backend/app/services/case_attempts.py`、`backend/app/services/dispatcher.py`、`backend/app/services/evaluations.py`、`backend/app/services/github_dispatch.py`、`backend/app/runner_contracts.py`、`runner-contract/schemas/v1/dispatch.schema.json`、`runner/README.md`、`runner/lib/json-schema-subset.mjs`、`runner/scripts/hydrate-dispatch.mjs`、`tests/backend/`、`tests/runner/`、`docs/architecture.*`、`plans/m1-02-fullstack-design/`。
- 不涉及：排队超时与 6 用例调度（#307/#315）、模型/数据集/Runner 执行逻辑、bad case 上报本体、`workflow_dispatch.inputs`。

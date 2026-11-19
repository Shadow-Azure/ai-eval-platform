# [m8-51] repository_dispatch 载荷传输层护栏与顶层字段收敛

[English](m8-51-dispatch-payload-guard.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-51-dispatch-payload-guard.*
  - backend/app/runner_contracts.py
  - backend/app/services/dispatcher.py
  - backend/app/services/github_dispatch.py
  - docs/architecture.*
  - plans/2026-11-19-pr*-review.md
  - plans/m1-02-fullstack-design/**
  - runner-contract/schemas/v1/dispatch.schema.json
  - runner/lib/json-schema-subset.mjs
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
- `client_payload` 顶层字段回到 10 个，同时保留 workflow 获取脚本包 pin 的能力。
- 区分 422：参数校验失败为终态，spam/限流仍退避重试。
- 补回归测试与文档，契约 schema 固化 10 个顶层属性上限。

## 方案

- `github_dispatch` 增加 `DispatchPayloadInvalid` 预检：`client_payload` 必须是 JSON object、最多 10 个顶层属性、序列化不超过 60KB（GitHub 硬上限 64KB），`event_type` 不超过 100 字符；错误信息包含实际字段数或字节数。
- dispatcher 捕获预检错误后直接 `_exhaust_dispatch`，写入 `dispatch_payload_invalid`，用例转 `dispatch_failed`，不调用 transport。
- 422 响应体不含 `spammed` 时标记为不可重试并终态失败；含 `spammed` 的 422 与 429、5xx、网络错误维持退避重试。
- `scripts_bundle` pin 只留在 outbox 行，不再复制进 `client_payload`；workflow 经分片 `/scripts` 元数据端点从 outbox 行取回 pin。
- `dispatch.schema.json` 增加 `maxProperties: 10` 并移除已不下发的 `scripts_bundle` 字段；Python 与 Node 的 schema 子集补 `maxProperties`/`minProperties`。

## 验收

- 完整 bundle 配置下 `client_payload` 为 10 个顶层键且序列化小于 60KB，dispatch 成功；`scripts_bundle` 仍在 outbox 行并可由 `/scripts` 端点读取。
- 11 键、超 60KB、超 100 字符 `event_type` 的 payload 在本地被拒绝，transport 未被调用，错误包含具体字段数或字节数。
- 422 参数错误不重试；422 spam、429、5xx 仍重试，均有单测覆盖。
- schema 拒绝 11 个顶层属性；Python 与 Node 校验器对 `maxProperties` 行为一致。
- `pytest tests/backend`、`npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。

## 范围

- 涉及：`backend/app/services/dispatcher.py`、`backend/app/services/github_dispatch.py`、`backend/app/runner_contracts.py`、`runner-contract/schemas/v1/dispatch.schema.json`、`runner/lib/json-schema-subset.mjs`、`tests/backend/`、`tests/runner/`、`docs/architecture.*`、`plans/m1-02-fullstack-design/`。
- 不涉及：排队超时与 6 用例调度（#307/#315）、模型/数据集/Runner 执行逻辑、bad case 上报本体、`workflow_dispatch.inputs`。

# 修复 TokenHub Anthropic 端点的模型连通性测试

[English](m8-model-connectivity-tokenhub.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - backend/app/api/model_configs.py
  - tests/backend/test_model_gateway.py
  - web/src/pages/ModelsPage.tsx
  - .agents/flow/issues/m8-model-connectivity-tokenhub.*
  - .agents/flow/issues/m8-runner-async-io.*
  - reports/2026-11-21/m8-model-connectivity-tokenhub/*
adr: []
github:
  number: 369
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/369
```

## 背景

生产 DeepSeek 配置走腾讯云 TokenHub 的 Anthropic 兼容端点（`https://tokenhub.tencentmaas.com/plan/anthropic`）。页面上的连通性测试会请求 `GET {base_url}/v1/models`，TokenHub 对模型列表返回 404，测试因此报「模型请求无效」，操作者反复替换一个实际有效的 Key。网关真正使用的 `POST {base_url}/v1/messages` 用同一把 Key 返回 200，说明失败来自探测路径而不是凭据。

## 目标

- 连通性测试改为按 `api_style` 调用网关实际使用的端点，并发送一条最小模型请求。
- 页面 Base URL 的辅助说明与新的探测行为一致。
- 补一条 TokenHub 形态（无模型列表、messages 可用）的回归测试。

## 验收

- Anthropic 配置在 `GET /v1/models` 返回 404 时仍能通过连通性测试，且只调用 `/v1/messages`。
- OpenAI 兼容配置仍探测 `/v1/chat/completions`，401/5xx 继续映射到 `secret` / `base_url` 字段。
- 目标后端测试与前端类型检查通过。

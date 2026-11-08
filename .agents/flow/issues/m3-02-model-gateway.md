# 管理模型凭据并建立集中式模型网关

[English](m3-02-model-gateway.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 24
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/24
```

## 背景

模型 Key 不能进入 Runner 或评测快照，Runner 只能使用按用例与 attempt 限定的短期凭据调用平台网关。当前没有模型配置、凭据加密、连通性校验和转发实现。

## 目标

- 建立 credential 与 model_config 表，使用信封加密保存密钥，支持掩码、轮换和撤销。
- 提供模型配置 API 与 OpenAI 兼容端点的连通性测试，临时配置不进入公共目录。
- 实现模型网关路由：校验短期 token、解密上游密钥、流式转发、统计用量并执行预算。
- 把供应商错误归一化为稳定错误码，并保证请求头和日志不包含凭据。

## 验收

- 明文 Key 不出现在 API 响应、日志、评测快照或 Runner 载荷中。
- 网关按 token 解析租户、模型配置与 attempt，拒绝未授权模型名和越权配置。
- 流式请求结束后用量写入 attempt，超预算返回 budget_paused。
- 鉴权失败、限流、供应商不可用和预算拒绝均有自动化测试。

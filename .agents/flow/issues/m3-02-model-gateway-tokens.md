# 集中式模型网关与短期模型凭据

[English](m3-02-model-gateway-tokens.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: done
scope:
  - .agents/flow/issues/m3-02-model-gateway-tokens.*
  - backend/**
  - runner/**
  - deploy/**
  - tests/**
  - pyproject.toml
adr: []
github:
  number: 46
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/46
```

## 背景

决策 D3 已确认：模型密钥只在控制面解密，Runner 使用短期令牌调用平台集中式模型网关，真实 Key 不进入 Runner。M3 的首个真实用例需要 OpenAI 兼容端点的统一转发，以及最小可用的凭据存储与用量记录；M2-05 已沉淀平台 HMAC 短期 token 模式可直接复用。

## 目标

- 实现模型配置与凭据存储：API 响应掩码返回，落盘走信封加密（首期可用主机密钥的简化信封实现，代码标注演进点）。
- 实现集中式模型网关端点：转发 OpenAI 兼容请求，统一超时、重试与错误映射。
- 实现 Runner 短期模型凭据：签发、校验、过期，复用 M2-05 的 `short_token` 线格式与互验测试模式。
- 记录最小模型用量与成本打点，按评测与分片可查。

## 验收

- 任何 API 响应中凭据不回显明文，仅显示掩码。
- Runner 持短期 token 经网关完成一次真实 chat completion（CI 可用 stub 端点，部署验证走真实端点）。
- 无效、过期或错配分片的 token 被网关拒绝，有明确错误分类。
- 用量打点落库并可按评测/分片查询；pytest 覆盖签发、过期、转发失败与掩码路径。

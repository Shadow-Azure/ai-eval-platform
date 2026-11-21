# 模型网关模型 token 改为 case 级

[English](m8-model-gateway-case-token.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/**
  - backend/app/**
  - backend/migrations/**
  - docs/**
  - plans/**
  - runner/**
  - tests/**
adr: []
github:
  number: 358
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/358
```

## 背景

生产 run `5a20991f` 中，6 个 case 同属一个 shard，模型 token 按 shard+attempt 签发；网关按 `(run, shard, attempt)` 查询 attempt 并 `.first()`，在某个 case 先结束后，仍在运行的 case 被误判 409 stale_attempt。详见 #358。

## 目标

- 模型 token 的授权与撤销都按 case attempt 绑定；同一 shard 内不同 case 互不影响。
- 网关按 case attempt 精确查询，只在该 case 的 attempt 终态时拒绝；run 级取消/预算暂停仍全局生效。
- 旧 token 兼容期只接受无 case 维度的 token，并按 shard 全终态校验；新 token 必须带 case 维度。
- 更新设计文档与模型网关契约，补多 case 同 shard 的回归测试。

## 验收

- 同 shard 一个 case 终态不影响另一个运行中的 case；409 只在调用方自身 attempt 终态时出现。
- 旧 token 兼容分支在截止时间前可用，之后移除；新 token 缺 case 维度时 fail closed。
- 设计文档与代码一致，回归测试通过。

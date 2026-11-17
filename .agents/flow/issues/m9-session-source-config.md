# 自进化上报 sessionSource 配置、历史回填与文档对齐

[English](m9-session-source-config.en.md) | 中文

```yaml flow
kind: issue
milestone: m9
priority: P2
status: ready
scope:
  - .github/**
  - backend/app/**
  - tests/**
  - docs/**
  - .agents/flow/**
  - plans/**
adr: []
github:
  number: 274
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/274
```

## 背景

评测 `c6b873b5-b01a-4074-8729-08762fcd3eef` 的 5 条 bad case 全部上报成功（HTTP 201），但平台 `SELF_EVO_SESSION_SIGNING_KEY` 与 `SELF_EVO_PUBLIC_BASE_URL` 为空，payload 没有 `sessionSource`；HE 的 root sync 全部 `SESSION_QUERY_UNAVAILABLE`。公网地址可以用现有 `ECS_HOST` 推导，签名密钥需要独立随机 Secret。已有 succeeded payload 已冻结，配置修复后不会自动补。

## 目标

- `deploy.yml` 从 `ECS_HOST` 推导 `SELF_EVO_PUBLIC_BASE_URL`（`http://${ECS_HOST}:8080`），显式 Secret 仍可覆盖。
- 提供 `backfill-self-evolution` 命令：为已有 succeeded、且有原生会话的 report 重建带 `sessionSource` 的 payload，并重置为 pending 等待 worker 重发。
- 修正 issue #263 的 `not_scored` 口径，补充架构文档中的 `sessionId` 回退语义。
- 补部署契约测试与 backfill 单测。

## 验收

- 部署契约测试断言 `SELF_EVO_PUBLIC_BASE_URL` 有 `ECS_HOST` fallback，且显式值优先。
- backfill 只处理有原生会话的 succeeded report；无轨迹、已有 source、未配置分别计数且不改行。
- 文档中英镜像；`verify:flow`、`lint:docs`、`verify:docs` 与相关 pytest 通过。

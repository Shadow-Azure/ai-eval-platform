# [m8-12] 修复 case token 300s TTL 导致的 heartbeat_timeout

[English](m8-12-case-token-ttl.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-12-case-token-ttl.*
  - .agents/flow/issues/m8-11-eval-skip-init-snapshot-injection.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - backend/app/api/runners.py
  - tests/backend/**
  - plans/**
adr: []
github:
  number: 206
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/206
```

## 背景

- 生产评测 de656d88（CodeSpec 资产流程验证，m8-11 修复后）：资产注入成功（16 files），agent 运行约 4.5 分钟，但评测在 571s 失败，case 报 `heartbeat_timeout`「300 秒内未收到 Runner 心跳」，trace 未上传。
- 平台给 shard dispatch 签发的 case token（`scope=case`）有效期硬编码为 300s（`backend/app/api/runners.py:615`），runner 用它发心跳和上传；token 过期后后端拒绝心跳，平台判定心跳超时。
- 这与配置的 case 超时（默认 3600s）无关：任何超过 5 分钟的用例都会失败并丢失轨迹，长流程（design→propose→apply）无法验证。

## 目标

- case token 有效期覆盖用例执行：至少 `case_timeout_seconds` + 余量（如 900s），或改为配置/可刷新。
- 保持安全：仍是短期签名凭证，仅在用例生命周期内有效，不影响 runner scripts 等其他 token。
- 测试：长 `case_timeout_seconds` 下 token 过期时间大于 300s；现有 dispatch/heartbeat 鉴权测试不回归。

## 验收

- 用例可超过 5 分钟（至配置超时）正常心跳、上传与落轨迹。
- 单测覆盖 TTL 计算与长超时场景；backend 相关测试通过。
- 部署后重跑生产评测，确认 design→propose→apply 与 trace。

# 规范生产部署密钥注入与换机迁移

[English](m7-deployment-secrets-migration.en.md) | 中文

```yaml flow
kind: issue
milestone: m7
priority: P1
status: in-progress
scope:
  - AGENTS.md
  - .agents/flow/issues/m7-deployment-secrets-migration.*
  - .github/workflows/deploy.yml
  - deploy/**
  - tests/deploy/**
  - plans/**
  - reports/**
adr: []
github:
  number: 116
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/116
```

## 背景

生产部署的长期密钥（SECRET_BOX_KEY、POSTGRES_PASSWORD、AUTH_SESSION_SECRET、GATEWAY_TOKEN_SECRET、RUNNER_TASK_SECRET、RUNNER_HEARTBEAT_TOKEN 等）目前由主机 `.env` 首次部署随机生成或手工维护；换机部署时若未完整迁移 `.env`，新密钥会导致已有凭据无法解密（模型 Key、阿里云连接全部失效），也无法保证部署可复现。

## 目标

- 将上述长期密钥统一纳入 GitHub Secrets，并由 Deploy workflow 写入生产主机 `.env`；主机 `.env` 只保留由 Secrets 注入的值与可重建的部署元数据。
- compose 将 worker 所需的 SECRET_BOX_KEY、RUNNER_HEARTBEAT_TOKEN、RUNNER_ADMIN_TOKEN 注入容器，api 补齐 RUNNER_ADMIN_TOKEN。
- 在 AGENTS.md 固化“部署必须支持换机迁移、长期密钥走 GitHub Secrets”的硬约束。

## 验收

- GitHub Secrets 中存在 SECRET_BOX_KEY、POSTGRES_PASSWORD、AUTH_SESSION_SECRET、GATEWAY_TOKEN_SECRET、RUNNER_TASK_SECRET、RUNNER_HEARTBEAT_TOKEN、RUNNER_ADMIN_TOKEN，且值与当前生产 `.env` 一致。
- Deploy workflow 在部署时把这些值写入主机 `.env`，并保留其他键；换机部署可用同一密钥解密已有凭据。
- `deploy/docker-compose.prod.yml` 的 api/worker 能读取对应 env；现有部署契约测试通过。
- AGENTS.md 包含换机迁移与 Secrets 注入的硬约束。

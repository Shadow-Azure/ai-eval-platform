# [m8-14] 平台正向代理打通评测容器的 GitHub 访问

[English](m8-14-github-egress-proxy.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-14-github-egress-proxy.*
  - .agents/flow/issues/m8-12-case-token-ttl.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - backend/app/config.py
  - backend/app/api/runners.py
  - runner/scripts/real-task.mjs
  - runner/lib/task-payload.mjs
  - scripts/doc-budgets.manifest.json
  - tests/backend/**
  - tests/runner/**
  - tests/deploy/**
  - deploy/**
  - .github/workflows/deploy.yml
  - docs/**
  - plans/**
adr: []
github:
  number: 209
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/209
```

## 背景

- 评测容器使用 `--network host`，网络与 runner 宿主机一致；宿主机到 GitHub（尤其 release 资产域名）的出口被限制或不稳定，install 下载 CLI tarball 报 `ETIMEDOUT`。
- 决策：采用平台正向代理方案（allowlist），不放开容器整条出网；容器通过 `HTTPS_PROXY` 访问 GitHub。
- 平台 backend ECS 有稳定 GitHub 出口，代理部署于此，runner 通过内网访问。

## 目标

- 后端新增配置 `RUNNER_EGRESS_PROXY`（默认空），随评测任务 payload 下发；新增**动态 egress token**（不使用静态账号密码）。
- 平台为每次评测签发短期签名 token（scope=egress，TTL=用例超时+余量，绑定 run/shard/attempt）；payload 携带；runner 把它作为代理凭据（Basic 用户名位）；代理侧 auth helper 校验签名/过期/绑定。
- 代理部署：私网监听（3128，VPC 内池可无认证）+ 公网监听（3129，动态 token 认证）；只放行 GitHub 域名，其余拒绝并记日志；`NO_PROXY` 保证平台/npm 镜像/OSS 直连；仅对需要的命令注入代理。
- 覆盖 VPC 固定池、阿里云竞态池、外部 Mac mini。
- 测试：token 签发/过期/伪造拒绝、runner 注入（含凭据）、代理域名白名单、私网/公网路径。

## 验收

- 评测容器经代理从 GitHub 实时下载；token 过期、伪造、非白名单域名均被拒绝；无静态账号密码。
- VPC 固定池/竞态池可走私网 3128；外部 Mac mini 经公网 3129 + 动态 token 可用。
- 白名单与访问日志可审计；测试与 CI 通过。

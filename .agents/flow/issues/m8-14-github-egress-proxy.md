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
  - deploy/**
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

- 后端新增配置 `RUNNER_EGRESS_PROXY`（默认空），随评测任务 manifest/payload 下发。
- Runner 在资产 install 与 agent 的 exec 环境注入 `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY`；未配置时行为不变。
- 部署带 allowlist 的正向代理（squid/tinyproxy）在 backend ECS，内网端口，仅 runner 内网可达；白名单仅放行 GitHub 相关域名，拒绝并记录其余请求。
- 白名单：`github.com`、`api.github.com`、`codeload.github.com`、`objects.githubusercontent.com`、`release-assets.githubusercontent.com`、`github-releases.githubusercontent.com`、`*.githubusercontent.com`。
- 测试：runner 注入/未注入、payload 携带代理、无配置兼容；部署文档说明白名单维护与验证命令。

## 验收

- 评测容器通过代理成功下载 CLI；design→propose→apply 可执行、trace 保留。
- 未配置代理时行为不变；CI 通过。
- 代理安全组仅允许 runner；访问日志可审计。

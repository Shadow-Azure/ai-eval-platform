# [m8-15] 代理镜像改为 CI 构建推送（修复 squid 标签失效导致的部署失败）

[English](m8-15-proxy-image-ci.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-15-proxy-image-ci.*
  - .agents/flow/issues/m8-14-github-egress-proxy.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/**
  - .github/workflows/deploy.yml
  - docs/**
  - tests/deploy/**
  - plans/**
adr: []
github:
  number: 211
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/211
```

## 背景

- #210 合并后生产部署失败：代理镜像构建报 `docker.io/library/squid:6.10: not found`（该标签在 Docker Hub 不存在），部署整体失败，回滚也失败，3129 未起来。
- 原方案在 ECS 宿主上现场 `docker compose` 构建代理镜像，需要宿主机访问 Docker Hub / Ubuntu archive，不适合生产且不可复现。

## 目标

- 代理镜像改由 CI 构建并推送 ACR：Dockerfile 基于可用的 `ubuntu/squid`；deploy workflow 的 build job 产出 `ai-eval-platform-proxy@digest`。
- 部署时直接拉取该镜像：Deploy 步骤传入 `PROXY_IMAGE`，`deploy.sh` 写入 `RUNNER_EGRESS_PROXY_IMAGE` 并把 proxy 纳入 `docker compose pull`；不再在宿主构建。
- 文档同步镜像来源（`ubuntu/squid`）。

## 验收

- 部署成功：proxy 容器运行，3128/3129 监听；3129 无 token 返回 407，带有效 token 可访问 GitHub。
- 生产评测经代理安装 GitHub CLI 成功，design→propose→apply 可执行。

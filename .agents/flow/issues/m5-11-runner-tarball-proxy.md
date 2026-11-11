# 以平台代理提供永久 Runner 安装包下载地址

[English](m5-11-runner-tarball-proxy.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P1
status: done
scope:
  - .agents/flow/issues/m5-11-runner-tarball-proxy.*
  - backend/**
  - tests/**
  - docs/**
  - deploy/**
  - .github/**
  - scripts/**
adr: []
github:
  number: 130
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/130
```

## 背景

PR #129 为阿里云竞价 Runner 在 OSS 缓存 actions-runner 包，部署时把预签名 GET URL 注入 `RUNNER_SPOT_RUNNER_TARBALL_URL`。SigV4 预签名 URL 最长 7 天：超过 7 天未重新部署时，新建竞价实例下载返回 403，cloud-init 安装失败、Runner 无法注册；对象仍在 OSS，只有地址过期。

## 目标

- 平台提供固定端点 `/runner/v1/runner-tarball/<version>.tar.gz`，由 api 用 OSS 凭据读取对象并流式返回，下载地址不再随预签名过期。
- 下载复用心跳 Token 鉴权，OSS 保持私有，不新增公共读或长期公开地址。
- 竞价 cloud-init 默认经该代理端点下载并携带 Bearer Token；显式配置 `RUNNER_SPOT_RUNNER_TARBALL_URL` 时按外部镜像处理且不发送 Token。
- 部署流水线只保证包已缓存到 OSS，不再把 7 天预签名 URL 写进主机 `.env`；未配置对象存储时回退 GitHub 直链。

## 验收

- 同一版本的固定代理地址可重复下载，部署间隔超过 7 天不影响新建实例。
- 错误或缺失 Token 返回 401，未知版本返回 404，对象存储未配置或读取失败返回 503/502。
- cloud-init 在代理模式携带 Token、外部镜像模式不带 Token；后端测试覆盖 URL 推导、鉴权与流式响应，部署契约测试覆盖不再注入预签名 URL。
- 文档说明代理端点、覆盖方式与 OSS 私有前提。

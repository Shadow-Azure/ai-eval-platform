# [m8-18] 修复 squid dstdomain 通配与子域重叠

[English](m8-18-squid-acl-wildcards.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-18-squid-acl-wildcards.*
  - .agents/flow/issues/m8-16-proxy-listen-fix.*
  - .agents/flow/issues/m8-17-squid-dstdomain-fix.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/**
  - tests/deploy/**
  - docs/**
  - plans/**
adr: []
github:
  number: 217
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/217
```

## 背景

- #216 的部署诊断显示下一处 squid 配置错误：`ERROR: 'github-releases.githubusercontent.com' is a subdomain of '.githubusercontent.com'`，配置继续 FATAL，代理未监听。
- 原因是 ACL 同时包含通配 `.githubusercontent.com` 和它的具体子域（objects/release-assets/github-releases）；squid 不允许通配与其子域共存于同一个 dstdomain ACL。

## 目标

- ACL 只保留两个通配：`.github.com`（覆盖 github.com 及其子域 api/codeload）与 `.githubusercontent.com`（覆盖 objects/release-assets/github-releases 等所有资产域名）。
- 更新部署契约测试与文档中的白名单描述。

## 验收

- squid 正常启动，3128/3129 监听；3129 无 token 返回 407。
- 生产评测 install 经代理成功，design→propose→apply 可执行。

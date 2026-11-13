# [m8-17] 修复 squid dstdomain 重复导致代理启动失败

[English](m8-17-squid-dstdomain-fix.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-17-squid-dstdomain-fix.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/**
  - tests/deploy/**
  - docs/**
  - plans/**
adr: []
github:
  number: 215
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/215
```

## 背景

- #214 的部署诊断直接给出了原因：squid 启动时报 `ERROR: '.github.com' is a subdomain of 'github.com'`，随后 `FATAL: Bungled ... acl github_domains dstdomain github.com .github.com ...`，squid 退出，3128/3129 未监听，评测 install `ECONNREFUSED`。
- 原因是白名单 ACL 同时写了 `github.com` 和 `.github.com`：squid 认为 `.github.com` 与裸域重复，直接拒绝该配置。

## 目标

- 移除冗余的 `.github.com`，保留 `github.com`、明确的子域（api/codeload/objects/release-assets/github-releases）与 `.githubusercontent.com`。
- 同步文档中的白名单描述；部署契约测试保持通过。

## 验收

- squid 正常启动，3128/3129 监听；3129 无 token 返回 407。
- 生产评测 install 经代理成功，design→propose→apply 可执行。

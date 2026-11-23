# [m8] Runner Node 下载遇网络瞬断时重试一次

[English](m8-node-download-network-retry.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-node-download-network-retry.*
  - runner/adapters/runtimes/claude-code.mjs
  - tests/runner/node-download-retry.test.mjs
  - plans/2026-11-23-pr400-review.md
adr: []
github:
  number: 397
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/397
```

## 背景

2026-11-23 10:50 CST，评测 `de8e73cc-1ff5-4bcf-a53c-7b2117f60d7e` 的唯一用例在环境准备阶段失败：`installScript()` 用 curl 从 `registry.npmmirror.com` 下载 Node 22.14.0 时 TLS 读取被对端重置，curl 以 56 退出。`docker exec` 把退出码透传给 runner，随后被折叠成 permanent 的 `agent_crashed`，没有重试，整个用例直接结束。

## 目标

- 只在 `runner/adapters/runtimes/claude-code.mjs` 的 `installScript()` 中为 Node 压缩包下载增加最多 1 次重试。
- 仅当 curl 退出码属于确认的网络类错误（6、7、28、35、52、55、56）时重试；HTTP 错误、证书、写文件失败、大小不符等确定性失败立即退出。
- 第二次仍失败时保留第二次 curl 的 stderr 与退出码，继续走现有失败链；不修改 `dockerRun`、失败分类、`max_attempts`、缓存或超时。

## 验收

- 7 个网络退出码首次失败后只重试一次并继续安装；5 个确定性退出码不重试且保留原退出码。
- 第二次网络失败时以第二次退出码和 stderr 结束，不执行后续安装步骤。
- `node --test tests/runner/*.test.mjs`、`npm test`、flow 与文档门禁通过。

# Runner 按 case 刷新 manifest 使用一次性连接

[English](m8-runner-manifest-fresh-connection.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - .agents/flow/**
  - plans/**
  - runner/**
  - tests/**
adr: []
github:
  number: 362
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/362
```

## 背景

#360 部署后，两个生产评测 run 的 6 个用例全部以 `runner_failed / model_token_unavailable` 失败，token 用量为 0。runner 在 `started` 回调之后用 `spawnSync` 加载镜像 tar（冷机约 42 秒）并 checkout 基线提交，同步阻塞了事件循环；平台 uvicorn 默认 5 秒关闭空闲 keep-alive 连接，runner 没来得及处理服务端的 FIN。随后 per-case manifest 刷新复用了这条已关闭的连接，`fetch` 立即抛 `UND_ERR_SOCKET`，而 #360 的 fail-closed 让这次传输失败直接结束用例。GitHub issue 的修复优先级是 P0；流程树按 m8 当前可动档位记为 P2，避免已完成的 m9 P0 issue 触发跨 milestone 串行门禁。

## 目标

- per-case manifest 刷新使用一次性、非池化连接，每次新建 TCP，不依赖 keep-alive 池的存活状态。
- 刷新失败时把 `error.cause.code` 写进 `model_token_refresh_failed` 日志与用例错误摘要。
- 保持 fail-closed：拿不到 case token 就不启动 Agent，不回退 shard token。
- 增加模拟“服务端关闭空闲连接 + 客户端同步阻塞”的回归测试。

## 验收

- 模拟回归测试通过：服务端关闭空闲连接且客户端同步阻塞后，刷新仍成功，且两次请求使用不同连接。
- 刷新仍失败时 fail closed，不启动 Agent，错误摘要带上传输层 cause。
- runner 契约测试与相关流程门禁通过。

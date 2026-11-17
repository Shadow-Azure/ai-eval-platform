# [m8-41] 修复模型网关 token 在 spot 长任务中过期

[English](m8-41-model-token-ttl.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-41-model-token-ttl.*
  - backend/app/api/runners.py
  - runner/scripts/real-task.mjs
  - runner/lib/mock-platform.mjs
  - tests/backend/test_runners.py
  - tests/runner/real-task-runtime-setup.test.mjs
adr: []
github:
  number: 269
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/269
```

## 背景

生产 5×SWE-bench run `5f344310-4690-4625-ab5e-8f7632e82f20` 中，3 个用例（`astropy-13033/13236/13398`）的 agent stdout 末尾都是 `401 token 已过期`。模型网关 token TTL 为 900 秒，而 run 的 `case_timeout_seconds=3600`、`agent_timeout_seconds=1800`；token 在 manifest 下发时签发，之后 spot 实例上 SWE-bench 镜像拉取/容器启动耗时 18–21 分钟，agent 真正调用模型时 token 已过期。

需要把模型 token 的签发时机和 TTL 与 agent 执行预算对齐：token 在镜像拉取完成、agent 启动时获取；TTL 至少覆盖 agent 超时（并受 case deadline 约束），而不是固定 900 秒。

## 目标

- runner 在 agent 启动前重新获取 manifest/模型 token，镜像拉取不再消耗 token TTL。
- manifest 端模型 token TTL = `min(case_timeout, max(默认 900, agent_timeout + 300))`；agent 超时 1800 时 token 至少 2100 秒。
- 后端与 runner 回归测试覆盖 TTL 计算和“启动时刷新 token”。

## 验收

- agent 启动时使用新签发的 token；镜像拉取超过 15 分钟后不会因 token 过期产生 401。
- 1800 秒 agent 在 token TTL 内完成；case deadline 仍能兜底。
- 新增测试通过；backend pytest 与 runner node tests 通过。

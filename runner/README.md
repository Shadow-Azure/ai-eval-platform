# Runner 参考实现（M2-03）

零依赖 Node.js 参考实现，用于验证 `runner-contract/` 的协议。`real-task.mjs` 在 dataset 任务上调用 Claude Code 与官方 F2P/P2P 评分；没有 repo 的合成任务仍走确定性 synthetic 分支。

## 测试

```sh
node --test tests/runner/*.test.mjs
```

## 本地执行

```sh
node runner/scripts/real-task.mjs \
  --dispatch /tmp/dispatch.json \
  --state-dir /tmp/eval-state \
  --object-store-root /tmp/eval-objects
```

`RUNNER_TASK_TOKEN` 与 `RUNNER_TASK_SECRET` 由 workflow / 平台注入；生产环境应替换为 OIDC 或短期 scoped token。

## 目录

- `lib/contracts.mjs`：加载 `runner-contract/schemas/v1` 并校验 payload。
- `lib/json-schema-subset.mjs`：仅覆盖本协议所用关键字的校验器。
- `lib/auth.mjs`：HMAC 短期 token（演示用，M2-04 替换）。
- `lib/dispatch-guard.mjs`：`dispatch_key` 原子 claim；平台数据库唯一约束是最终底线。
- `lib/object-store.mjs`：content-addressed 本地对象存储；生产替换为 OSS。
- `lib/callback-client.mjs`：带 `Idempotency-Key` 的有界重试回调。
- `lib/mock-platform.mjs`：契约测试用的平台 stub。
- `workflows/eval-shard.yml`：私有 Runner 仓 workflow 模板；M2-05 激活。

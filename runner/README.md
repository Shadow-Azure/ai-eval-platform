# Runner 参考实现（M2-03）

零依赖 Node.js 参考实现，用于验证 `runner-contract/` 的协议。`lib/adapters.mjs` 提供通用 registry：core 只调度 manifest/token/workspace/上传/回调，dataset adapter（`adapters/datasets/*`）负责环境镜像、test patch 与 report 解析，runtime adapter（`adapters/runtimes/*`）负责安装/启动 Agent；SWE-bench 与 Claude Code 是内置插件。`real-task.mjs` 在 dataset 任务上调用 Claude Code 与官方 F2P/P2P 评分；没有 repo 的合成任务仍走确定性 synthetic 分支。

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

## 用例级执行（M4-01）

一次 `repository_dispatch` 仍只对应一个分片。workflow 先运行 `plan-cases.mjs` 把清单展开为用例级 matrix，再让每个执行 Job 只跑一个用例：

```sh
node runner/scripts/plan-cases.mjs \
  --dispatch /tmp/dispatch.json \
  --state-dir /tmp/eval-state

node runner/scripts/real-task.mjs \
  --dispatch /tmp/dispatch.json \
  --case <case_run_id> \
  --state-dir /tmp/eval-state \
  --object-store-root /tmp/eval-objects
```

- 本地 claim 以 `dispatch_key:case_run_id` 为键，同一用例的重放会在启动 Agent 前以 `duplicate_dispatch` 退出；跨机器去重仍由平台 `started`/`completed` 幂等兜底。
- `started` 返回 429（评测并发已达上限）时，Runner 用同一 `event_id` 退避重试，最长 `RUNNER_CAPACITY_WAIT_SECONDS`（默认 1800 秒）；超时后释放 claim 并以 `callback_retryable` 退出，attempt 保持 `queued`，不会误标失败。

## 长任务心跳（M4-02）

`real-task.mjs` 在 `spawnSync("docker", …)` 中执行 Agent 时会阻塞事件循环，单次 `started` 心跳不足以覆盖长用例。真实仓库路径会在 Agent 启动前拉起 `scripts/heartbeat-agent.mjs` 子进程，按 `RUNNER_HEARTBEAT_INTERVAL_MS`（默认 30s）持续发送心跳；用例结束或异常时先 SIGTERM、最多等 5s 再 SIGKILL。

- 心跳 payload 不含 `runner_id`（`heartbeat` 契约是 `additionalProperties: false`，Runner 绑定由 `started` 与 `X-Runner-Id` 证明）。
- 子进程只从环境读取短期 token，不进入 argv 或日志；`RUNNER_HEARTBEAT_DISABLE=1` 可关闭。
- 平台侧五类超时（排队、执行、评分、上传宽限、心跳）由对账 worker 收口；心跳只证明控制脚本存活，Agent stdout 静默不等于死亡。

## 原生轨迹采集（M6-01）

`real-task.mjs` 在用例结束时从隔离 HOME 的 `.claude/projects/` 收集主/子 Agent 的会话 JSONL 与 `*.meta.json`，连同 `trace/claude-stream.jsonl`、`trace/claude-stderr.log` 和失败诊断一起按 attempt 上传。`lib/trace-capture.mjs` 负责安全逻辑路径、digest、采集完整性判定与重复摘要去重；宿主绝对路径不进入清单。失败执行同样保留已生成文件，只有 stdout 时标记 `partial`。

## 目录

- `lib/contracts.mjs`：加载 `runner-contract/schemas/v1` 并校验 payload。
- `lib/json-schema-subset.mjs`：仅覆盖本协议所用关键字的校验器。
- `lib/auth.mjs`：HMAC 短期 token（演示用，M2-04 替换）。
- `lib/dispatch-guard.mjs`：`dispatch_key` 原子 claim；平台数据库唯一约束是最终底线。
- `lib/manifest-source.mjs`：manifest 拉取与 digest 计算，供 `real-task` 与 `plan-cases` 共用。
- `lib/object-store.mjs`：content-addressed 本地对象存储；生产替换为 OSS。
- `lib/callback-client.mjs`：带 `Idempotency-Key` 的有界重试回调。
- `lib/mock-platform.mjs`：契约测试用的平台 stub。
- `scripts/plan-cases.mjs`：把分片清单展开为用例级 matrix 的 prepare 入口。
- `scripts/heartbeat-agent.mjs`：长任务心跳 sidecar。
- `workflows/eval-shard.yml`：私有 Runner 仓 workflow 模板；M2-05 激活，M4-01 起为 prepare + 每用例 matrix。

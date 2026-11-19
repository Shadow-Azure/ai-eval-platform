# [m8-45] 平台排队超时改为分片进度租约，修复固定 3600 秒误杀

[English](m8-45-queue-progress-lease.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/**
  - backend/**
  - plans/**
  - tests/**
adr: []
github:
  number: 307
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/307
```

## 背景

分片派发时平台把分片内全部 attempt 统一标成 `queued`；reaper 从同一时刻起按固定的 `eval_queue_timeout_seconds`（默认 3600 秒）判排队超时，且 `queue_timeout` 不可重试。一个分片经常包含多波 Job：`shard_size=64`、有效并发 8、用例 60 分钟时，第 1 小时只跑完第一波，约 48 个从未 `started` 的 attempt 被直接判死；并发 1 时约 62 个。问题在平台的 attempt 状态机，不在 GitHub job 的 `timeout-minutes`。

## 目标

- 排队预算改为分片级进度租约：初始预算为 `case_timeout + 600s`，分片内任意 attempt `started` 或已运行 attempt 进入终态时，为仍在排队的兄弟 attempt 续期。
- 只有整片连续无进度超过预算才判 `queue_timeout`；租约绝对上限 23 小时（82800 秒），低于 GitHub 自托管 Job 的 24 小时排队上限。
- 心跳不续期：心跳只证明进程活着，不能证明分片在推进。
- 升级前已存在的排队 attempt（没有租约列）回退旧逻辑，不因迁移报错；`queue_timeout` 事件写入排队等待时长与进度快照，详情页能区分排队超时与执行超时并显示排队等待时长。

## 方案

- 配置保留 `eval_queue_timeout_seconds` 作为旧行回退值，新增 `eval_queue_timeout_grace_seconds=600` 与 `eval_queue_timeout_max_seconds=82800`。
- `eval_case_attempt` 新增可空列 `queue_started_at` 与 `queue_deadline`；dispatcher 在分片真正派发成功时写入初始租约。
- started 与已运行 attempt 的终态路径批量续期同分片仍排队的 attempt；reaper 只读 `queue_deadline`，为空时回退 `phase_changed_at + eval_queue_timeout_seconds`。
- `case.timeout` 事件补充 `queue_wait_seconds`、`last_progress_at`、`queue_deadline`、`shard_size`、`concurrency`，错误摘要写明实际等待时长。

## 验收

- `shard_size=64`、并发 8、用例 60 分钟：1 小时时不再出现约 48 个 `queue_timeout`；分片内每波开始/结束都会续期，直到全部用例完成。
- `shard_size=64`、并发 8、用例 30 分钟：同样不误杀，后排 attempt 能继续等待并开始。
- 一个分片连续 70 分钟没有任何 attempt `started`：仍判 `queue_timeout`。
- 分片内有 attempt 正在运行、其余排队：等待中的 attempt 不因 60 分钟到期被误杀；运行中的 attempt 仍受 `case_timeout` / `heartbeat_timeout` 管。
- 租约到达 23 小时上限后停止续期并按超时处理，不得无限延长。
- 升级前已存在的 `queued` attempt（没有 `queue_deadline`）回退旧 3600 秒逻辑，不因迁移报错。
- `tests/backend` 新增租约/续期/回退/上限测试通过；`npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。

## 范围

- 涉及：`backend/app/config.py`、`backend/app/models/evaluation.py`、`backend/migrations/versions/`、`backend/app/services/dispatcher.py`、`backend/app/services/case_attempts.py`、`backend/app/services/lifecycle.py`、`backend/app/services/reaper.py`、`tests/backend/`。
- 不涉及：GitHub execute job 的 `timeout-minutes`（由 #304 跟踪）、spot 池可靠性与容量核算（由 #306 跟踪）、`shard_size` 默认值本身、新的执行后端或队列系统。

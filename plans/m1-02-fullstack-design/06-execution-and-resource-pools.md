# 06 · 调度、并发与资源池

## 1. 结论：可以并发，但并发由真实 Runner 决定

GitHub Actions 自托管 Runner + 阿里云竞价 ECS 能支持用例级并发。一个临时 Runner 同时执行一个 Job，因此：

```text
实际并行 Job 数 = min(
  当前在线且可调度的 Runner 数,
  eval_run.requested_concurrency,
  resource_pool.max_capacity,
  tenant_remaining_quota,
  GitHub/平台剩余调度能力
)
```

竞价池的 `max_capacity=50` 不代表立即有 50 并发；只有实例创建、Runner 注册并上线后才增加实际并发。没有空闲 Runner 时 Job 在 GitHub 排队。官方限制包括每 workflow run 的 matrix 最多 256 Job、自托管 Job 排队 24 小时、执行最长 5 天；平台必须用更短的用例超时和心跳超时主动回收。

## 2. 状态机：执行与结果分离

### 2.1 评测状态

```text
preparing → queued → running → finalizing → completed
                                      └────→ partial
                                      └────→ failed
queued/running → pausing_budget → paused_budget
paused_budget → queued（确认增加预算后）
queued/running/finalizing → cancelling → cancelled
preparing/queued → failed
```

- `completed`：全部用例执行与评分终态，且没有失败/超时/取消用例。
- `partial`：全部逻辑用例已收口，但至少一个用例失败、超时或取消，仍有可用结果。
- `failed`：平台级准备/调度/聚合失败，或没有任何有效结果。
- 轨迹导入不参与 `completed/partial` 判定，单独显示 `trace_status`。
- `paused_budget` 不是终态，不生成最终指标；页面展示已花费、预算、已完成/未完成数量和增加预算入口。

### 2.2 attempt 执行状态

```text
pending → dispatching → queued → leased → running → scoring → uploading → succeeded
                         │          │         │        │         └→ artifact_missing
                         │          │         │        └───────────→ failed
                         │          │         └────────────────────→ timeout
                         │          └───────────────────────────────→ timeout/cancelled
                         └──────────────────────────────────────────→ dispatch_failed
```

逻辑用例的 `execution_status` 取最新有效 attempt；`result_status` 取 `resolved/unresolved/not_scored`。重试创建新 attempt，旧 attempt 不变。

### 2.3 合法迁移与事务

每次回调在单事务中完成：校验当前 attempt → 校验 sequence/状态迁移 → 更新 attempt/case/run → 写 eval_event → 写通知 outbox。非法迁移返回 409 并记录审计，不允许旧回调把 succeeded 改回 running。

## 3. 分片与 GitHub 工作流

### 3.1 为什么不直接把 500 个用例塞进一个 matrix

- matrix 硬上限为 256；大 payload 还受 repository_dispatch 体积和 GitHub 元数据约束。
- 一个 workflow run 过大，失败重跑和对账粒度差。
- 资源池只有少量 Runner 时，一次发布数百个 Job 只会形成长队列，不增加吞吐。

建议默认 `shard_size = 64`，可根据池容量在 16–64 调整，硬上限 256。500 用例可形成 8 个分片（7×64+52），每分片一个 workflow run，Job 仍是用例级。

### 3.2 Dispatch 只传引用

```json
{
  "tenant_hint": "default",
  "eval_run_id": "eval_01...",
  "shard_id": "shard_01...",
  "dispatch_key": "eval_01...:shard_01...:1",
  "manifest_url": "https://platform.example/runner/v1/shards/shard_01.../manifest",
  "manifest_digest": "sha256:..."
}
```

完整用例、资产快照、模型配置和回调凭据不进入 GitHub payload。`tenant_hint` 也可省略；生产更建议只传 opaque ID。

### 3.3 通用 workflow 结构

```yaml
on:
  repository_dispatch:
    types: [eval-shard]
permissions:
  contents: read
  id-token: write
jobs:
  prepare:
    runs-on: [self-hosted, linux, eval]
    steps:
      # GitHub OIDC → 平台短期 token → 获取并校验 shard manifest
      # 输出 case attempt IDs 的 matrix，不含题目、密钥或大字段
  execute:
    needs: prepare
    strategy:
      fail-fast: false
      matrix:
        case: ${{ fromJson(needs.prepare.outputs.cases) }}
    runs-on: ${{ fromJson(needs.prepare.outputs.runs_on) }}
    steps:
      # 原子 claim dispatch/attempt，重复 workflow 在启动 Agent 前退出
      # 启动任务容器与一用例一沙箱
      # OIDC 换 case token；经模型网关调用模型
      # 执行 Runtime、评分、OSS 上传、平台回调
```

实际工作流不能使用任意用户输入拼接 shell；所有值通过环境变量和严格 schema 校验。`fail-fast:false` 防止一个用例失败取消整片。

## 4. 身份与短期凭据流转

```text
GitHub Job → 请求 GitHub OIDC token
平台       → 校验 issuer/audience/repository/workflow/ref/run_id/job_id
           → 校验该 GitHub run/job 与已登记 dispatch 的关系
           → 签发短期、case/attempt-scoped token
Runner     → 调 manifest/upload/callback/model-gateway
平台       → 按 attempt 撤销或在终态自然过期
```

OIDC 是建议默认，避免把平台长期 Secret 固定在 Runner 或把短期 token 暴露在 repository_dispatch。MVP 技术验证需要确认自托管 Runner 的 OIDC 环境、平台时间同步和重试时 token 刷新；若暂用 GitHub Secret 中的平台 bootstrap token，必须标记为安全降级并尽快替换。

## 5. 调度器与对账

调度器循环：

1. 领取可用 outbox/shard，设置 lease 和 `dispatch_key`。
2. 校验 run/pool/attempt 仍可派发，写 dispatching。
3. 调 GitHub repository_dispatch，保存 workflow 对账信息。
4. 未收到 started 的分片按 1/5/15 分钟退避查询 GitHub，而非立即重复派发。
5. 超过 dispatch 超时且无匹配 run 才允许新 dispatch；有匹配 run 则进入对账/取消流程。
6. GitHub `workflow_job` webhook 仅用于补充 queued/in_progress/completed 和容量信息，平台回调仍是业务事实来源。

Redis 锁用于降低并发重复，DB 唯一约束才是最终幂等底线。Dispatcher 多副本不能依赖单机内存队列。

## 6. 阿里云竞价供给

### 6.1 扩容输入

```text
queued_matching_jobs
- idle_online_runners
- starting_instances × 每实例 Runner 数
= capacity_gap
```

按池上限、租户配额、实例规格库存、当前竞价、池配置的价格阈值和冷却时间分批扩容。已确认 MVP 只使用竞价实例，不自动切换到按量实例；当前价格高于阈值时不创建实例，排队原因统一为 `resource_price_above_threshold`，资源池和评测页展示当前价格、阈值、区域/规格、最近检查时间与下次检查时间。供给器可消费 `workflow_job(queued)` webhook，同时周期性查询平台 queued shard 作为补偿；webhook 丢失不能成为永不扩容的理由。

### 6.2 实例生命周期

```text
waiting_for_price → requested → creating → cloud-init/user_data → registering → online → busy/idle
                                                              ├→ draining → released
                                                              ├→ interruption_suspected → offline
                                                              └→ heartbeat_lost → recovering/released
```

- user_data 只取得一次性 JIT Runner 注册信息，注册为 `--ephemeral`，绑定池标签和唯一 runner 名。
- 每个实例默认一个 Runner；多 Runner 必须显式配置 CPU/内存/磁盘和沙箱隔离上限。
- 空闲缩容先确认无 active Job，再从 GitHub 注销，最后释放 ECS。
- Spot 回收通知只作为优化，不作为可靠性前提；任何实例都可能无预警失联，最终由 heartbeat/Job 对账和重试兜底。
- 创建失败和库存不足进入退避；价格超上限保持 `waiting_for_price` 并周期性重新询价。两类情况都向评测页发布明确的资源池容量事件，不将 queued 用例误标 failed，也不承诺恢复时间。

### 6.3 重试策略

| 故障 | 默认处理 |
|---|---|
| Spot 回收、Runner 失联、网络中断 | 同用例自动新 attempt，最多 2 次；使用指数退避 |
| Agent 进程非零退出 | 依据适配器错误分类；环境类可重试，确定性配置错误不重试 |
| 模型限流/5xx | 尊重 provider retry-after，计入用例超时与预算 |
| 评分镜像失败 | 与 Agent 失败分开记录；可只重试评分，避免重跑 Agent |
| OSS 上传失败 | 保留沙箱短暂存活并指数重试，直到用例上传宽限期结束 |
| 回调丢失但 OSS 完整 | 对账 worker 依据清单补建完成事件 |
| 配置错误、凭据无权限、commit 不存在 | fail fast，不自动重试 |

## 7. 超时、心跳与取消

- 平台同时维护 GitHub queued 超时、Agent 执行超时、评分超时、上传宽限期和 heartbeat 超时，不用一个总超时掩盖所有故障。
- heartbeat 只证明控制脚本活着；长工具调用可发送阶段/用量心跳。Agent stdout 静默不必然代表死亡。
- 取消先更新 `cancelling` 并撤销短期 token，再请求 GitHub cancel workflow/job；Runner 收到 SIGTERM 后上传已存在产物并退出。
- 失联 Runner 无法保证本地进程立即停止，因此取消后的迟到结果按 attempt 状态处理，不覆盖取消事实，可保留用于诊断。

## 8. 模型预算暂停与恢复

达到评测模型预算时，平台执行“暂停”，不是把整次评测判为失败：

1. 调度器立即停止新分片和新 attempt 派发，run 进入 `pausing_budget`。
2. 模型网关拒绝新的模型调用并返回稳定错误 `budget_paused`，防止后台 Agent 继续产生费用。
3. Runner 为已打断 attempt 上传已有轨迹和产物，记录 `pause_reason=budget_exceeded`；已完成评分保留。
4. 所有活跃 attempt 安全退出后，run 进入 `paused_budget`。暂停不等于远程进程可冻结，恢复时被打断用例通过新 attempt 重跑。
5. 用户在确认框中看到当前花费、新增预算和剩余用例，确认后调用 `resume-budget`。
6. 服务端增加预算与 budget_version、写审计，run 回到 queued；只调度未完成用例，已解决/未解决且已完成的用例不重跑。

预算暂停期间不自动切换模型、不切换按量资源、不生成最终结果。预算并发控制和实际花费以模型网关记录为准，Runner 自报只作补充。

## 9. 容量示例与演进阈值

例：500 用例、8 个在线 Linux Runner、requested concurrency=8、每用例 20 分钟，则约 8 路并行、63 波，理想执行约 21 小时，另计启动/评分/重试；增加到 32 个 Runner 后理论约 5.3 小时。实际排期还受分片、模型限流和实例启动时间影响。

以下情况应启用架构预留的队列后端：持续需要 50–100 以上并发、要求秒级确定性派发、GitHub 排队/限流成为主要故障，或需要比 workflow_job 更精细的公平调度。切换时保留 in-job 执行脚本、任务包、回调与 OSS 契约，只替换 ExecutionBackend。

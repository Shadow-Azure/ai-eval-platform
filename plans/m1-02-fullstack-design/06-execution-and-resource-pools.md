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
| Spot 回收（provider 确认停止） | 同用例自动新 attempt；spot 预算最多 3 次，且不超过 run 的 `max_attempts` |
| Runner 退出、心跳丢失（实例仍 Running） | 记为 `runner_lost` 的普通可重试失败，计入 `max_attempts`，不消耗 spot 预算 |
| 网络中断 | 同用例自动新 attempt，受 `max_attempts` 约束；使用指数退避 |
| Agent 进程非零退出 | 依据适配器错误分类；环境类可重试，确定性配置错误不重试 |
| 模型限流/5xx | 尊重 provider retry-after，计入用例超时与预算 |
| 评分镜像失败 | 与 Agent 失败分开记录；可只重试评分，避免重跑 Agent |
| OSS 上传失败 | 保留沙箱短暂存活并指数重试，直到用例上传宽限期结束 |
| 回调丢失但 OSS 完整 | 对账 worker 依据清单补建完成事件 |
| 配置错误、凭据无权限、commit 不存在 | fail fast，不自动重试 |

## 7. 超时、心跳与取消

- 平台同时维护 GitHub queued 超时、Agent 执行超时、评分超时、上传宽限期和 heartbeat 超时，不用一个总超时掩盖所有故障。
- 平台排队是分片进度租约：初始预算为 `case_timeout + 600s`；分片内任意 attempt `started` 或已运行 attempt 进入终态，就为仍在排队的兄弟 attempt 续期；只有整片连续无进度超过预算才判 `queue_timeout`。租约绝对上限 23 小时（82800 秒），低于 GitHub 自托管 Job 的 24 小时排队上限。心跳不续期，避免僵尸分片无限延长。
- 升级前已存在的排队 attempt 没有租约列，reaper 回退到 `phase_changed_at + eval_queue_timeout_seconds`；`queue_timeout` 事件记录排队等待时长、最近进度时间、租约截止时间、分片大小与并发，详情页据此区分“排队超时（未启动）”与“执行超时（跑过 Agent）”。
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

## 竞争态池零常驻与 warm pool（M8-34 / M8-48）

竞争态 spot 池默认不保留常驻实例（`min_capacity=0`）：无 queued job 时 `current_size=0`。每个 shard job 临时创建一台 spot 实例 +
一个 ephemeral runner；runner 退出后实例立即关机/terminate，controller 以 Runner 可接单状态而非实例状态判定容量。
GitHub `workflow_job` webhook 触发 reconcile，30s 轮询兜底；容量公式 `desired = queued_jobs - (online+registering)`。
冷启动由预烘焙 AMI/预拉镜像承担。
M8-48 允许 `min_capacity≥1` 的小规模 warm pool：`min_capacity` 按 Runner 数解释，无排队时也补足到该下限，
仍受 `max_capacity`、价格阈值和单批创建上限约束；默认 0 保持零常驻语义。

## 竞价资源 SpotDuration 保底（M8-35）

竞价实例在创建时按本次评测最长 `case_timeout_seconds` 向上取整设置 Aliyun `SpotDuration`（1–6 小时），
使 job 在保底窗口内不被竞价回收。并发批次取最大超时；超过 6 小时时不能静默创建无保底实例，应明确
回退按量/fixed 或报可诊断错误。`SpotDuration` 写入实例 cost_json 便于排障，不改变零常驻语义。


## spot 中断快速重试（M8-37）

消费 `provider_stopped` 后，平台立即把该实例上的 active attempt 标为 `spot_interrupted`，释放 runner 绑定，
并重新申请原竞争态 spot 池；同一 case 的 spot 重试最多 3 次且不超过 run 的 `max_attempts`，达 `max_attempts`
时以 `spot_interrupted` 终态收口，spot 预算耗尽时标 `spot_retry_exhausted`。
默认不切 fixed/on-demand；可选配置兜底池，由 M8-46 实现。这样不再把云侧停止伪装成 300s `heartbeat_timeout`。


## Runner 绑定与 spot 中断映射（M8-38）

started 回调使用 `RUNNER_ID || RUNNER_NAME`，确保 `eval_case_attempt.runner_id` 落库；Runner 的 online 心跳保留
`busy_case_attempt_id`，只有 terminal callback、`release_case` 或 offline 才清空。这样 provider_stopped 时
`_reclaim` 能通过 attempt.runner_id 或 busy 绑定找到 active case，触发 M8-37 的快速重试。


## 竞争态池调度可靠性（M8-44）

手动重试的 `case.retry_requested` 使用平台保留的正数序号，不占用新 attempt 的 sequence 0，因此
`case.started` 的 0 只属于 Runner；started 遇到唯一键冲突时回读事件并幂等返回，历史冲突行把平台事件迁入保留区间后重试，不返回 500。

execute job 在 `started` 前失败（脚本包、checkout、dispatch 校验或执行异常）时，由独立失败步骤用 manifest 端点的
短期 token 回调 `runner_bootstrap_failed`，平台按普通重试预算立即结算，queue timeout 仅作兜底。失败步骤不依赖
脚本包或 checkout，因而脚本包不可用时仍能结算。

`_reclaim` 按真实原因分流：只有 provider 确认停止（`provider_stopped`、`spot_interrupted`）走 spot 中断；`runner_exited`
与 `runner_heartbeat_lost` 走 `runner_lost` 普通重试，错误摘要保留 reason、provider_state、detail 与 last_heartbeat，
并计入 run 的 `max_attempts`，不消耗 spot 预算。

有效并发在池级按 in-flight attempt 预留：一个评测的 cap 会扣除其他评测已释放到池中的 attempt 数；started 再对全池
running/scoring/uploading attempt 做 `max_capacity` 硬闸，因此两条评测并发时 Agent 数不超过池上限。

分片进度租约与 queue timeout 的改造由 #307 单列，不重复实现。

## 竞争态池 warm pool、lost grace 与 Runner 清理（M8-48）

容量缺口取排队缺口与 warm pool 缺口的最大值：`queue_gap = max(0, queued - idle - starting*slots)`，
`warm_gap = max(0, min_capacity - live - starting*slots)`，`desired = min(max_capacity, current_size + ceil(max(queue_gap, warm_gap)/slots))`。
`min_capacity` 是 Runner 数下限，默认 0 时行为与 M8-34 零常驻一致；价格阈值、`max_capacity` 与单批创建上限仍生效。

`eval_spot_runner_lost_grace_seconds` 默认 180 秒，覆盖 2–3 分钟冷启动后的注册窗口和短暂心跳抖动；只有 Runner
持续 offline 或心跳超过 `heartbeat_timeout + grace` 才回收，已恢复的 Runner 不会被一次短抖动带走。缩容跳过注册不满
`eval_spot_runner_min_idle_seconds`（默认 120 秒）的 Runner，避免刚注册就被 drain；已运行过 Job 的 Runner 不受影响。

Worker 定期调用 Runner 清理：只删除超过 `eval_runner_offline_retention_seconds`（默认 7 天）的 offline 行，
且该行没有活跃 attempt 或非终态实例引用。终态 attempt 与已释放/中断实例保留历史，但先释放指向被删行的外键指针；
实例的 `cost_json.runner_name` 仍可用于排障。容量统计使用 `online/busy` 白名单并显式排除历史状态，offline/draining 行不参与容量。

## Runner 脚本包预置与获取顺序（M8-47）

脚本包仍是 M5-12 的确定性内容寻址 tar，名称 `eval-runner-scripts-<md5>.tar.gz`，存放在私有 OSS；

本阶段只改变 Runner 获取它的路径：

1. spot 实例启动时仍可在 `/opt/eval/runner-scripts/` 预置脚本包。镜像可直接烘焙；没有烘焙时 cloud-init 用平台在创建实例时生成的短时私有 OSS 预签名 GET 预取，并用 sha256 校验后落盘。预取是 best-effort，失败不阻塞实例启动；dispatch 载荷不含 pin 时该缓存不作为 workflow 的直达来源。
2. dispatcher 把本次分片冻结的 `scripts_bundle`（name/md5/sha256/size）写到 outbox 行，不写入 repository_dispatch payload；workflow 通过分片 `/scripts` 元数据端点从 outbox 行取回 pin，`client_payload` 保持 10 个顶层键。
3. prepare/execute 的脚本包获取使用元数据返回的私有 OSS 直连 `oss_url`、平台 `/runner/v1/scripts/<name>` 代理，最后才回退 `actions/checkout`。网络下载校验 sha256/md5；总预算 60 秒。
4. 获取失败由 #308 的 `runner_bootstrap_failed` 在 started 前结算，平台按普通重试预算生成下一次 attempt，不等待 queue timeout。
5. 实例上不写 OSS secret 或平台长期 token；预签名 URL 是短时下载能力，随 cloud-init user-data 一起清理。

发布步骤：OSS 由 deploy 的 `scripts/cache-runner-scripts.py` 上传；镜像缓存用同一脚本的 `--out-dir /opt/eval/runner-scripts` 生成文件与 `manifest.json` 后烘焙进镜像。两处使用同一 md5/sha256，便于互相校验。

## dispatch payload 传输层护栏与字段收敛（M8-51）

`repository_dispatch.client_payload` 最多 10 个顶层属性、序列化后小于 64KB，`event_type` 不超过 100 字符。平台在 POST 前本地校验 10 个顶层键、60KB 字节预算与 100 字符 `event_type`：违反时 outbox 直接 `failed`，写入 `dispatch_payload_invalid`（含具体字段数/字节数），用例转 `dispatch_failed`，不调用 GitHub transport，也不消耗重试预算。`runner-contract/schemas/v1/dispatch.schema.json` 增加 `maxProperties: 10`，Python 与 Node 的 schema 子集同步支持 `maxProperties`/`minProperties`。

422 按响应体分类：参数校验错误（属性数、长度、大小等）是终态；包含 `spammed` 的 422 与 429、5xx、网络错误仍按退避重试。`scripts_bundle` pin 只保留在 outbox 行，dispatch 载荷不再复制，顶层字段回到 10 个；代价是 spot 启动多一次 `/scripts` 元数据请求，正确性、评分与轨迹不变。

## spot 连续失败回退 fixed/按量（M8-46）

同一 run 连续出现 provider 侧 spot 回收，或容量控制器连续多次因库存不足/价格高于阈值无法扩容时，平台把该 run
迁到已有的固定/按量资源池。回退不新增云厂商，也不自动创建新池：运维在 spot 池 `instance_config` 中设置
`fallback_pool_id`、`fallback_after`（连续失败阈值）和可选的 `fallback_max_price_cny`（成本上限）。

- 回退状态写入 `eval_run.resource_pool_snapshot_json.spot_fallback`：来源/目标池、触发原因、连续失败次数、阈值、
  成本快照与激活时间；同时写 run 级 `eval.spot_fallback` 事件、源/目标池的 `resource.spot.fallback` 资源事件。
- 回退在重建 retry 之前完成，因此新 attempt 的 `resource_pool_id`、dispatch `runs_on` 与 manifest 都指向兜底池；
  尚未 dispatch 的 outbox 行会一起改写。已经发送到 GitHub 的旧 spot Job 保持原样，其后续 retry 使用新池。
- 已回退的 run 不再消耗 spot 重试预算，只受 run 的 `max_attempts` 约束；这避免了回退阈值达到后仍直接记为
  `spot_retry_exhausted`。provider 回收路径的实际阈值取 `min(fallback_after, max_attempts-1)`，确保至少留一次 attempt
  在兜底池执行；库存/价格阻塞路径没有 attempt 消耗，直接使用 `fallback_after`。一个用例成功完成会重置连续失败计数。
- 启用 `fallback_max_price_cny` 时，兜底池必须通过 `instance_config.reference_price_cny`（或已有价格）声明小时成本；
  成本未知或超过上限时不回退，记录 `resource.spot.fallback_suppressed` 与 `suppressed_reason`，继续既有 spot 重试。
- 评测详情「执行组合」新增“资源回退”行：未触发、已回退（来源 → 目标、连续次数）或被成本策略拦截。资源池详情展示
  配置的兜底池与阈值，资源事件列表新增回退/拦截事件类型。

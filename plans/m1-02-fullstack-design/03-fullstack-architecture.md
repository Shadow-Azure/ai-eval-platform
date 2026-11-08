# 03 · 全栈架构设计

## 1. 架构原则

1. **控制面与执行面分离**：FastAPI/PostgreSQL 管理事实，GitHub/Runner 只接收可重放的执行指令。
2. **不可变输入与可变状态分离**：数据集版本、资产包、模型配置快照不可变；eval_run、eval_case_run 和事件随执行变化。
3. **大对象与查询事实分离**：OSS 保存轨迹原件、报告和产物，数据库保存索引、摘要、归一化元数据与权限归属。
4. **通知与事实分离**：SSE 是低延迟通知，不是唯一数据源；任何页面都可从 DB 重建状态。
5. **适配器稳定接口**：数据集、Runtime、轨迹、资源供给和执行后端都通过注册表扩展。

## 2. 逻辑视图

```text
Browser (React + Ant Design)
  │ HTTPS / SSE
  ▼
FastAPI modular monolith
  ├─ identity / tenancy / audit
  ├─ datasets / metrics / assets
  ├─ models / credentials ─────► Aliyun KMS
  ├─ resources / provisioning ─► Aliyun ECS
  ├─ evaluations / scheduling ─► GitHub repository_dispatch
  ├─ callbacks / ingestion ────► OSS + PostgreSQL
  ├─ model gateway ────────────► MiniMax / DeepSeek / GLM / Kimi / MiMo
  └─ query / SSE ◄───────────── Redis pub-sub + DB event cursor
                                      │
                                      ▼
                        PostgreSQL (facts + normalized traces)
                        Redis (locks, dedupe, pub-sub, short cache)
                        OSS (task bundles, traces, reports, artifacts)

Private GitHub repository → generic workflow → ephemeral self-hosted Runner
  → job container → per-case sandbox → Agent runtime
  → scoring image → reports/artifacts/raw traces to OSS
  → heartbeat/completion callback to FastAPI
```

模型网关是 FastAPI 控制面的独立模块，可先同进程部署，但必须保持独立路由、鉴权、限流与审计边界。它兑现“Runner 只拿短期凭据”的架构要求；如果未来直接把真实供应商 Key 注入 Runner，将构成明确的安全降级，而不是等价实现。

## 3. 后端模块与依赖方向

```text
api routes
  ▼
application services (transaction, authorization, idempotency)
  ▼
domain models + ports
  ▼
infrastructure adapters (SQLAlchemy, GitHub, Aliyun, OSS, KMS, Redis)
```

| 模块 | 主要职责 | 关键端口 |
|---|---|---|
| `identity` | 登录、会话撤销、角色、默认租户 | PasswordHasher、SessionStore |
| `datasets` | 来源解析、校验、版本与用例、任务视图 | DatasetAdapter、ImportStore |
| `metrics` | 注册指标、计算用例/评测聚合 | MetricAggregator registry |
| `assets` | Git 扫描、Ref 解析、快照、Runtime 映射 | SourceProvider、RuntimeAdapter |
| `models` | 模型配置、凭据生命周期、供应商校验 | SecretBox、ModelProvider |
| `resources` | 连接、池、Runner 心跳、实例供给 | Provisioner、RunnerProvider |
| `evaluations` | 创建、分片、状态机、取消、重试、聚合 | ExecutionBackend、EvalRepository |
| `ingestion` | 回调接收、OSS 清单、轨迹与评分导入 | TraceParser、ScoreParser |
| `realtime` | DB 事件读取、Redis 通知、SSE 回放 | EventStream |
| `delivery` | 部署与迁移记录、健康查询 | DeploymentProvider |

API 层不直接写 SQL，GitHub/Aliyun SDK 不进入 domain。跨模块写操作由 application service 组合，禁止资源模块反向依赖评测页面 DTO。

## 4. 前端分层

```text
routes/pages → feature components → TanStack Query / SSE hooks
             → typed API client (OpenAPI generated)
             → shared UI primitives + design tokens
```

- Ant Design 负责 Form、Select、Table、Modal、Drawer、Pagination、Message 等通用控件。
- 业务组件自研：状态堆叠条、指标卡、容量卡、资产文件树、事件时间线、会话树、waterfall、span 属性面板。
- 服务端状态由 Query 管理；向导草稿、当前 span 等本地状态留在页面或受控 store，不把全部状态放全局。
- API 类型由 OpenAPI 生成，SSE 事件与错误码共享同一契约来源。

## 5. 核心写路径

### 5.1 创建评测

```text
Web → prepare：鉴权 + 配置校验 + Ref 解析 + 资产冻结 + 容量检查
    → PostgreSQL transaction：preparation / asset snapshot / task manifest
Web → create：Idempotency-Key + preparation_id
    → transaction：eval_run + eval_case_run + outbox
    → scheduler：领取 outbox、创建分片、repository_dispatch
    → GitHub：按池标签分配 Job
```

创建接口提交后即使响应丢失，客户端用同一幂等键仍可得到原 eval_run。Dispatcher 崩溃后从未发送的 outbox 恢复；GitHub 已接收但发送记录未落库时，通过 `dispatch_key` 对账，不能盲目无限重发。

### 5.2 用例完成

```text
Runner → request signed upload URLs
Runner → OSS raw trace / score report / artifacts
Runner → complete callback (manifest digest + metrics + status)
API   → verify task token, attempt, manifest and payload hash
DB    → idempotent case update + event/outbox
Worker→ parse score + normalize trace + finalize attempt
DB    → aggregate snapshot → SSE notification
```

评分与轨迹导入可以分别重试。完成回调不等待轨迹归一化结束，避免长解析阻塞 Runner；但最终评测状态必须等待必要的评分导入，轨迹失败不阻断评分终态。

### 5.3 实时读取

```text
页面打开 → GET snapshot（含 event_cursor）
        → SSE after=cursor
DB commit → Redis publish（仅 run_id / event_id）
SSE worker→ 按游标从 DB 读完整事件 → 推送
断线重连 → Last-Event-ID → DB replay → 继续实时
```

Redis 重启只丢通知不丢事实；客户端收到 gap 或非法游标时重新拉快照。状态应用按 `(case_run_id, attempt_no, sequence)` 单调递增，忽略旧 attempt 和重复事件。

## 6. 部署单元

| 单元 | MVP 部署 | 扩展方式 |
|---|---|---|
| Web | 静态构建，由 Nginx/Caddy 同源托管与反向代理 | CDN/独立静态站 |
| API | ECS Compose 两个进程副本起步 | 无状态水平扩容 |
| Dispatcher/Worker | Compose 独立容器，DB 锁保证单飞任务 | 按导入/调度/轨迹拆分 worker |
| Model gateway | 与 API 同部署、独立模块和资源限额 | 独立服务多副本 |
| PostgreSQL/Redis/OSS/KMS | 阿里云托管或受控服务 | 不改领域接口 |
| Runner | 私有仓 repo 级临时 Runner，Linux Docker | 自研队列时复用 in-job 脚本 |

## 7. 关键风险与架构防线

| 风险 | 防线 |
|---|---|
| API Key 泄露到 Runner 或日志 | 模型网关、短期 scoped token、日志脱敏、禁止明文快照 |
| 不可信代码逃逸 | 私有仓、任务容器 + 用例沙箱、非 root、禁宿主机 Docker socket、网络白名单 |
| GitHub 与平台状态不一致 | 平台状态为权威、回调幂等、workflow_job 对账、超时回收 |
| 大轨迹拖垮数据库 | DB 存摘要与索引，大正文/原件进 OSS，按需加载 |
| 多租户越权 | 所有业务键带 tenant_id，复合唯一约束，仓储层强制作用域与跨租户测试 |
| 指标口径漂移 | 数据集版本和指标版本快照，聚合器重算可审计 |
| 部署回滚破坏数据库 | expand-contract、备份、向后兼容窗口、分批破坏性迁移 |

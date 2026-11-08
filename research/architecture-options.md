# AI 评测平台候选架构方案（讨论稿）

> 2026-11-08 · 依据三份调研：agent-eval-platform-survey.md（评测框架）、2026-11-08-github-actions-runner-pool.md（资源池）、reports/session-trace-research.md（轨迹存储）
> 状态：候选方案，待讨论定稿。定稿后按 docs/ 三件套规范重写进正式文档。

## 0. 调研给出的硬约束（三案共通）

- 没有现成的「评测平台 + GitHub Actions 资源池」开源产品，**平台层（数据集/模型凭据/评测编排/实时进度/指标展示/多租户预留）必须自建**。
- SWE-bench instance schema 是数据集事实标准；评分复用 swe-bench harness（Docker 容器内 apply patch + 跑测试），不要重写评分器。
- 自托管 runner 一 runner 一 job，**并发度 = 在线 runner 数**；matrix ≤256/run；无空闲 runner 时排队、延迟不可控；私有仓库自托管 runner 的收费政策 2026 年内有变数。
- 执行不可信 AI 代码必须沙箱隔离：Docker 基线 → Firecracker microVM 收口；macOS 无 Docker，用 tart/lima microVM 或把容器类用例路由到 Linux runner。
- 轨迹归一化：统一 span 树模型（属性兼容 gen_ai.* / openinference.*），归一化入库为主、原始 JSONL 归档 OSS 为辅；Claude Code / Codex / OpenCode / pi 各有原生格式，txcript、toolpath 两个开源项目有现成解析器与字段映射可参考。

## 1. 三案对比总表

| 维度 | 方案 A：GitHub Actions 原生调度（推荐 MVP） | 方案 B：Harbor/EvalScope 执行底座 + Actions 调度 | 方案 C：自研 worker + 队列 |
|---|---|---|---|
| 执行触发 | 平台 → repository_dispatch → matrix workflow → 每用例一 job | 同 A，job 内改调 harbor/evalscope CLI | 平台 → 自有队列（Redis/SQS）→ worker 长轮询 |
| 执行器代码量 | 中：自研 in-job runner 脚本（拉用例→起沙箱→驱动 agent CLI→收结果） | 小：框架自带 agent 适配与沙箱 provider | 大：worker 管理、沙箱、心跳、扩缩容全自建 |
| Agent 适配 | 自研适配器（4 个 agent × stream-json） | Harbor 已带 claude-code/codex/openhands 等 | 同 A |
| 实时进度 | job 内心跳 POST 平台 API + SSE 推送（需自建） | 同 A | 天然支持（worker 直连平台） |
| 并发上限 | 在线 runner 数；MVP 够用 | 同 A | 取决于 worker 资源，可上百 |
| 延迟确定性 | 排队延迟不可控 | 同 A | 秒级、可控 |
| 平台依赖 | 依赖 GitHub（计费政策风险） | GitHub + 框架双重依赖 | 只依赖自己的基础设施 |
| 多租户演进 | 需从 Actions 模型中抽象出「资源池」概念 | 同 A | 平滑 |
| 上线速度 | 最快 | 快 | 慢 |
| 适合阶段 | MVP 与成长期（<50 并发） | 想最快验证多 agent 矩阵 | 规模化/商业化阶段 |

## 2. 方案 A 详设（推荐）

### 2.1 总体结构

```text
┌─ 阿里云 ─────────────────────────────────────────────┐
│  ECS: 平台服务 (docker compose)                        │
│    ├─ Web 前端 (React + Ant Design)                   │
│    ├─ API 服务 (NestJS)                               │
│    ├─ 调度器 (dispatcher: 分片/触发/回传/超时)           │
│    └─ 实时推送 (SSE)                                  │
│  RDS PostgreSQL: 业务库 + 轨迹归一化库                  │
│  Redis: 队列/缓存/锁                                  │
│  OSS: 原始 session JSONL / 评测产物 / 构建镜像层         │
│  (可选) SAE/ACK: 容器化托管演进                         │
└──────────┬───────────────────────────────────────────┘
           │ repository_dispatch + 回调 API
┌──────────┴───────────────────────────────────────────┐
│  GitHub: eval-runner 仓（workflow + 镜像构建 CI）      │
│    workflow: matrix(N≤256) → 每用例一 job              │
└──────────┬───────────────────────────────────────────┘
           │ runs-on labels 路由
  ┌────────┴─────────┬──────────────────┐
  │ Mac mini runner  │ 阿里云 Linux      │ 后续: 更多 Linux
  │ (ephemeral, tart)│ runner (Docker)   │ runner / ARC 池
  └──────────────────┴──────────────────┘
```

### 2.2 一次评测的生命周期

1. 用户在平台配置：数据集版本 × agent 运行时 × skill × 模型凭据 × 资源池。
2. 平台创建 eval_run，把用例清单分片（每片 ≤256）写库，dispatcher 对每个分片发 repository_dispatch（payload：eval_run_id、分片、回调地址、超时）。
3. runner 上的 job 执行 in-job runner 脚本：拉取用例 → 在沙箱（Docker/tart VM）内准备环境 → 以 headless 模式驱动 agent CLI（claude -p --output-format stream-json / codex / opencode / pi）→ 采集产物与会话文件 → 跑 swe-bench harness 评分 → 产物打包。
4. 回传双通道：job 内心跳/结果 POST 平台回调 API（实时进度）；产物与原始 session JSONL 上传 OSS，平台异步导入归一化轨迹。
5. 平台聚合指标：按数据集注册的指标定义（如 resolve rate）计算 eval_run 级结果，SSE 推送进度到前端。
6. 用户从用例行点击跳转：eval_case_run → session（主/子 agent 树）→ span 时间线。

### 2.3 核心数据模型（多租户预留）

- tenant（单租户期只有一条记录，全表带 tenant_id）
- user / credential（模型 API Key，密文存储，KMS 或凭据加密）
- dataset / dataset_version / case（case 存 SWE-bench 兼容 schema + 数据集自定义 JSONB）
- agent_runtime / skill（版本化）
- resource_pool / runner（登记自托管 runner 元数据与 labels）
- eval_run / eval_case_run（状态机 + 指标快照 + session 关联）
- metric_definition（按数据集注册指标计算方式）
- session（parent_session_id 主/子树 + token/成本汇总 + raw_uri）/ span / event（属性 gen_ai.*、openinference.*、eval.* 前缀）

### 2.4 关键设计点

- **Agent 适配器接口**：`start(env, prompt, model_cfg) → stream events; collect_sessions() → raw files; normalize() → span tree`。接口语义对齐 Harbor 的 agent adapter 约定，未来可整体替换为 Harbor 实现。
- **评分器不改写**：SWE-bench 用官方 harness 的 Docker 评分镜像，平台只解析其 report.json。
- **实时进度**：不依赖 GitHub API 轮询作为唯一来源（有限流），以 job 内心跳为准，check-runs API 仅作对账。
- **安全**：eval-runner 仓必须私有；runner 上不存任何长期凭据（用 OIDC/短期 token 或 job 内平台换发的短期凭证）；agent 代码一律在沙箱内执行。
- **演进开关**：dispatcher 抽象出 `DispatchBackend` 接口（GitHubActionsBackend / QueueBackend），并发 >50~100 或出现秒级调度需求时新增 QueueBackend，runner 脚本几乎不动。

## 3. 方案 B 差异点

- in-job runner 替换为 `harbor run -d <dataset@version> -a <agent> -m <model> --env docker`（或 EvalScope agent bridge）。
- 平台自建部分不变：数据集/凭据/进度/指标/轨迹 UI 仍自建；轨迹采集可复用 EvalScope 的 agent_trace 录制思路。
- 取舍：少写 agent 适配器，但引入年轻框架的稳定性风险与二次开发限制（Harbor license 待确认；EvalScope Apache-2.0 较稳）。

## 4. 方案 C 差异点

- 阿里云上常驻 worker 服务（Linux，Docker 沙箱），Mac mini 上跑 worker 客户端；worker 长轮询平台队列取任务。
- GitHub Actions 回归 CI/CD 本职（合入自动部署）。
- 实时进度、超时控制、优先级队列、配额全部可控；代价是首期多 2~4 周的调度与运维开发。

## 5. 部署与 CI/CD（三案共通）

- 阿里云购买清单（引导购买时细化）：ECS（2C4G 起步，跑平台容器）、RDS PostgreSQL（入门版）、Redis（云数据库或 ECS 自建）、OSS（标准型）、按量 Linux runner（或在现有 ECS 上叠加）。
- 镜像构建：平台镜像 + 评测基础镜像推 ACR；GitHub Actions workflow 负责。
- 发布流水线：main 合入 → CI（test + lint + 镜像构建推 ACR）→ 迁移门禁（Prisma/Flyway 迁移先在 RDS 执行，失败阻断）→ SSH/compose 滚动更新 ECS → 健康检查回滚。

## 6. 待讨论决策点

1. 执行层：A 自研 in-job runner（推荐）vs B 接 Harbor/EvalScope。
2. 后端技术栈：NestJS/TypeScript（推荐，与前端同语言）vs Go vs Python(FastAPI)。
3. 轨迹 UI：自建轻量轨迹查看器（推荐，满足用例→session 跳转）vs 自托管 Langfuse 嵌入。
4. 数据库：RDS PostgreSQL（推荐）确认；是否接受 Redis 初期由 ECS 自建。
5. MVP 范围：是否首期就接 4 个 agent，还是先做 Claude Code + Codex 两个。

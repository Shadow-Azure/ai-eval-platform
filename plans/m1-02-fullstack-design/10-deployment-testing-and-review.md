# 10 · 部署、测试与 Review 指南

## 1. GitHub 到阿里云的交付流水线

### 1.1 main 合入后的顺序

```text
pull_request：lint/test/contract/migration dry-run/build verification
       │ merge to main
       ▼
main：全量测试 + 文档/流程门禁
  → 构建 Web/API/Worker 镜像并推 ACR（commit SHA + immutable digest）
  → 生成 SBOM/依赖与安全扫描
  → RDS 备份/恢复点确认 + Alembic 当前版本检查
  → expand migration（单飞、失败即停）
  → ECS Compose 滚动更新 API/Worker/Web
  → /healthz + /readyz + DB/Redis/OSS/GitHub/模型网关检查
  → 小范围 smoke：登录、读数据集、创建准备、SSE、轨迹查询
  → 记录 deployment；失败按兼容矩阵回滚应用或向前修复

已确认 main 合入后的常规迁移与部署不增加人工审批。应用回滚、破坏性 contract（删列/改约束）和生产资源连接变更高风险操作进入独立审批工作流；审批不阻塞常规持续交付。
```

GitHub Actions 建议通过 OIDC assume Aliyun RAM Role，减少长期云 AK；部署环境使用 GitHub Environment 保护规则。SSH 私钥若必须使用，仅存 GitHub Environment Secret，限制来源与权限，不进入 Runner 评测工作流。

### 1.2 数据库 expand-contract

| 版本 | 数据库 | 应用 | 回滚能力 |
|---|---|---|---|
| N | 旧 Schema | 旧应用 | 基线 |
| N+1 expand | 加表/加列/加索引，保留旧列 | 旧应用仍可运行 | 可回滚应用 |
| N+1 deploy | expand 完成 | 新应用双写/读新结构 | 可回滚到兼容旧镜像 |
| N+2 backfill | 数据回填与校验 | 新应用 | 以前向修复为主 |
| N+3 contract | 加约束/删旧列 | 只使用新结构 | 不承诺自动数据库回滚 |

原型中的“自动回滚”应理解为自动恢复兼容的应用镜像，而不是无条件执行 down migration。破坏性迁移失败或健康检查失败时阻断并告警，禁止自动 downgrade 生产库。

## 2. 可观测性与运维

- 结构化日志包含 request_id、tenant_id、eval_run_id、case_run_id、attempt_no、github_run/job_id、parser_version；不包含 token、Key、密码和完整题面/轨迹。
- 指标：API 延迟/错误、dispatch 延迟、queued/running 数、Runner heartbeat、Spot 创建成功率/中断率、回调冲突、OSS 上传失败、轨迹解析失败、SSE 重连/gap、模型网关限流和成本。
- 告警：迁移失败、readyz 失败、全部 Runner 离线、queued 接近 24h、轨迹失败率异常、临时凭据销毁失败、模型网关认证失败突增。
- 审计：登录、凭据轮换/临时销毁、资源连接、供给测试、发起/取消/重试、raw 下载、部署回滚。

## 3. 测试策略与故障注入

| 层 | 必测内容 |
|---|---|
| Domain unit | 状态机、SWE-bench resolved/total 口径、F2P/P2P 测试明细、分片、重试分类、容量公式、租户权限 |
| Adapter contract | 数据集 golden、Runtime 资产映射、四类轨迹 fixture、评分报告 |
| DB integration | 复合租户外键、唯一幂等、事件游标、重试 attempt、迁移与回滚兼容 |
| GitHub integration | dispatch 丢失/重复、matrix 拆分、workflow_job 对账、OIDC 换 token、排队 |
| Aliyun integration | 库存不足、创建超时、Spot 回收、缩容 active Job 防护、费用上限 |
| Runner E2E | Agent→评分→OSS→回调→归一化→结果完整链路 |
| Realtime | 重复/乱序事件、Redis 重启、SSE 断线、DB 回放、旧 attempt 迟到 |
| Security | Key 不出现在 payload/log/DB 快照、沙箱隔离、raw 权限、轨迹 XSS |
| Web E2E | 七条核心流程与空/加载/失败/无权限/部分成功/危险确认 |
| Deployment | 空库/上一版迁移、健康检查失败、应用回滚、contract 禁止自动回滚 |

最低发布演练应真实完成一次“小批量 SWE-bench + 两个 Linux Runner + 一个子 Agent + 一次 Spot/进程中断 + 一次 SSE 重连”，仅用 mock 无法证明并发与轨迹闭环。

## 4. 诉求到设计的追溯矩阵

| 诉求 | 原型 | 后端/数据 | 专项设计 |
|---|---|---|---|
| 登录 | 02 §2/§3.3 | 04 Auth、05 §3 | 03 D2 |
| 数据集与扩展 | 02 §3.1 | 04 Dataset、05 §4 | 07 §1–2 |
| 模型与 Key | 02 §3.3 | 04 Models/模型网关、05 §5 | 03 §2 |
| 资源池 | 02 §3.4 | 04 Pools、05 §5 | 06 §6 |
| 发起组合 | 02 §3.5 | 04 Prepare/Create | 07 §5 |
| 单租户可扩展 | 02 §1 | 05 §1/§3 | 01 §6 |
| 实时与结果 | 02 §3.6 | 04 Events/Results、05 §6–7 | 06 §2、07 §3 |
| 会话元数据与跳转 | 02 §3.7 | 04 Trace、05 §7 | 08 全文 |
| 主/子 Agent | 02 §3.7 | trace_session/span | 08 §3–6 |
| 并发与竞价 | 02 §3.4/§3.6 | attempt/resource/provisioner | 06 全文 |
| 自动部署与迁移 | 02 部署页 | deployment | 10 §1 |

## 5. 决策记录与评审状态

### 5.1 已确认的核心决策（2026-11-08）

| 场景 | 已确认结论 | 设计落点 |
|---|---|---|
| 模型凭据 | 使用平台集中式模型网关，统一转发、鉴权和监控；真实 Key 不进入 Runner | 03、04 §7 |
| 执行与评分 | 执行状态/解决结果分离，未通过测试完整上报测试级明细和日志 | 04、05、07 |
| 重试 | 每次真实执行是独立 attempt，保留旧结果、轨迹和成本 | 05 §6、06 §2 |
| 轨迹采集 | 实时事件预览 + 最终原生文件校正 | 08 §5–6 |
| 主/子 Agent | 按 Runtime 使用不同原生关联证据；本机 Claude/Codex/OpenCode 日志结构已纳入适配设计 | 08 §4 |
| SWE-bench 指标 | `% Resolved = resolved / total`，错误和未完成不扣分母；指标分母由数据集版本配置 | 07 §3 |
| 资源供给 | 只用竞价；超过价格阈值就等待并展示资源池原因，不自动切换按量 | 06 §6 |
| 数据库发布 | 采用通用 Parallel Change / expand-migrate-contract，应用可回滚，数据库不无条件 down | 05 §9、10 §1 |
| 轨迹权限 | 元数据/摘要、脱敏内容、完整 thinking/raw 分级授权并审计 | 08 §8 |

### 5.2 已确认的运行策略（2026-11-08）

| 问题 | 已确认结论 |
|---|---|
| 结果对比基线 | 自动推荐最近的可比最终评测，允许手动更换；显示推荐原因和配置差异 |
| 租户与资源权限 | 首轮只实现单租户和必要操作权限，不引入 Owner/平台管理员等复杂角色体系；tenant/role/归属字段保留扩展口 |
| 原始轨迹保留 | 全部长期保留，包括原始轨迹、完整输出和失败产物；未来再评估生命周期成本 |
| 模型成本预算 | 达到预算后暂停；用户确认增加预算后恢复，未完成用例继续，被打断用例使用新 attempt |
| 部分结果分享 | 仅单租户登录用户可见，不提供公开分享链接 |
| 并发与分片 | 默认并发 min(8, 在线容量, 池上限)，分片默认 64；作为可调工程参数 |
| 部署与回滚 | main 合入后自动迁移和部署；回滚与破坏性 contract 才需要审批 |

## 6. 建议实施切片

```text
A 契约与骨架：登录/默认租户/OpenAPI/Alembic/Web 主题与路由
B 配置中心：数据集导入、模型/KMS、Git 资产、连接与池
C 最小执行闭环：单 Runtime + 单 Linux Runner + SWE-bench + 评分/OSS/回调
D 并发与可靠性：分片、多 Runner、Spot、重试/取消、事件与 SSE
E 轨迹闭环：Claude Code 主/子 Agent + 三栏 viewer + raw 降级
F 扩展与交付：其他 Runtime fixtures、指标扩展、部署与故障演练
```

每个切片都通过真实 API/DB 驱动页面；开发 fixtures 可以 seed，但生产运行时没有按环境切换的 stub 业务分支。

## 7. Review checklist

- [ ] 十一条诉求都有页面、API、表和专项验收，不把“支持扩展”停留在 JSONB 字段。
- [ ] 模型 Key 不进入 dispatch、Runner 磁盘、日志、评测快照或浏览器持久化。
- [ ] GitHub matrix/queue 限制、Spot 启动延迟与回收都已纳入并发和重试。
- [ ] 重试不覆盖旧 attempt，回调/SSE 可幂等和恢复。
- [ ] 数据集指标口径在运行快照和终态下可解释，失败用例不会被隐藏。
- [ ] 主/子 Agent 关系来自原生证据，pi 分支/fork 不误判为 subagent。
- [ ] 轨迹归一化失败不阻断评分，raw 可访问且受权限/审计控制。
- [ ] 单租户没有多余管理 UI，多租户归属与配额从首轮存在。
- [ ] main 自动部署包含迁移门禁、健康检查和兼容的回滚边界。
- [x] 首轮核心决策与运行策略已确认；评审代码契约后固化为 M1-03 双语详细设计与实现 issue。

## 8. 主要外部依据

- GitHub Actions 限制：[Actions limits](https://docs.github.com/en/actions/reference/limits)。
- GitHub 自托管 Runner 自动伸缩：[Autoscaling with self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/autoscaling-with-self-hosted-runners)。
- 仓库既有调研：[GitHub Actions Runner Pool](../../research/2026-11-08-github-actions-runner-pool.md) 与 [Agent Eval Platform Survey](../../research/agent-eval-platform-survey.md)。
- Runtime 与轨迹官方来源见 [08 §2](08-trace-normalization.md#2-官方资料与可复用机制)。
- SWE-bench 结果语义：[Evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/)、[reporting.py](https://github.com/SWE-bench/swe-bench/blob/main/swebench/harness/reporting.py)、[grading.py](https://github.com/SWE-bench/swe-bench/blob/main/swebench/harness/grading.py)。
- 数据库兼容变更：[Parallel Change / expand-migrate-contract](https://martinfowler.com/bliki/ParallelChange.html)；该模式明确适用于数据库重构、滚动部署和蓝绿发布。

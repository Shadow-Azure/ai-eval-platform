# 调研：复用 GitHub Actions 自托管 Runner 作为评测执行资源池

> 日期：2026-11-08 · 标注：✅=已从官方来源验证，⚠️=未验证/需实测，🔗=来源

## 0. 结论（先看这里）

- ✅ **机制可行**：自托管 runner 免费可用（硬件自付），默认一个 runner 同时只跑一个 job，平台并发度 = 在线 runner 数；用 `runs-on` labels 把任务路由到 Mac mini / Linux 机。适合 **<50 并发、分钟级延迟可接受** 的评测场景。来源：[About self-hosted runners](https://raw.githubusercontent.com/github/docs/main/content/actions/concepts/runners/self-hosted-runners.md)
- ✅ **计费政策有变动风险**：GitHub 曾宣布 2026-03-01 起对私有仓库自托管 runner 收 $0.002/分钟"cloud platform charge"，**因社区反对已宣布推迟**，但明确表示会重新评估——2026 年内可能回归。来源：[GitHub Changelog 2025-12-16](https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-Experience-for-github-actions/)
- ✅ **安全红线**：官方长期警告自托管 runner 不应用于公开仓库（fork PR 可执行任意代码并窃听 runner 环境、内网与凭据）。评测平台若执行不可信 AI 代码，**必须配沙箱隔离**，不能裸跑。来源：[Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)、[社区讨论 #26722](https://github.com/orgs/community/discussions/26722)
- ✅ **硬上限**：job matrix ≤256 job/run；job 排队 24h 超时、执行 5 天上限；每 5 分钟每 repo/org 最多注册 1500 runner；runner group ≤10000；workflow 触发限流 1500 事件/10s/repo。来源：[Actions limits](https://docs.github.com/zh/actions/reference/limits)

## 1. 官方限制与计费 ✅

- **单 runner 单 job**：默认并发=1（job 级别），横向扩容唯一方式是加 runner 或 ephemeral 池。
- **runner 级 vs 计划级并发**：GitHub 托管 runner 的 20/40/60/500 并发是托管侧限制；自托管不受此限，只受你机器数限制。
- **私有仓库用自托管 runner 无需 Team/Enterprise**（硬件自己出）；runner groups 管理功能在 GHEC/GHES 文档树中提供（free 组织可用 repo 级 runner + labels 代替）。来源：[manage-access](https://docs.github.com/zh/enterprise-cloud@latest/actions/how-tos/manage-runners/self-hosted-runners/manage-access)
- **并发组 concurrency**：workflow 或 job 级 `concurrency: <group>` 保证同组同时只跑一个 run/job，其余排队（新功能 `queue: max` 可入队至 100，超限拒绝）；`cancel-in-progress: true` 可取消组内旧 run。可作"同一评测任务串行/防重"手段。来源：[Control workflow concurrency](https://docs.github.com/fr/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- **路由优先级**：GitHub 按 `runs-on` labels+group 找在线且空闲的 runner；60s 未接走重新入队；无匹配 runner 则排队至 24h 超时失败。来源：[self-hosted-runners reference](https://raw.githubusercontent.com/github/docs/main/content/actions/reference/runners/self-hosted-runners.md)

## 2. 弹性 Runner 池 ✅

- **官方推荐 ARC**（actions/actions-runner-controller，K8s）：Runner ScaleSet Listener 长轮询 GitHub → `Job Available` 时拉起 EphemeralRunner pod（JIT token 注册）→ 一 job 一 pod，跑完即销毁。支持 dind、kubernetes 容器模式、自定义 runner 镜像。来源：[ARC 文档](https://raw.githubusercontent.com/anthonysidesapps/docs/b52f875ef278c5589352c61141ff5c9e0cf6744d/content/actions/concepts/runners/actions-runner-controller.md)
- **无 K8s**：官方新推 **Runner Scale Set Client**（[actions/scaleset](https://github.com/actions/scaleset)，Go 库，公测）面向 VM/裸机自建 autoscaler，支持 Linux/macOS/Windows；社区成熟方案 [philips-labs/terraform-aws-github-runner](https://github.com/philips-labs/terraform-aws-github-runner)（webhook `workflow_job` → 扩缩 EC2/Spot）。
- **ephemeral runner**：`config.sh --ephemeral` 一 job 一注册、跑完自动注销，GitHub 官方推荐用于 autoscaling（持久 runner 不保证停机不被派活）。
- **Mac mini**：✅ macOS 11+ 支持 runner；⚠️ ephemeral 可行——裸进程模式 `--ephemeral` + launchd 守护循环重装；容器化模式 Mac 上 Docker 需 VM（lima/colima），**Apple Silicon + Linux 容器评测建议直接跑 Linux 机而非 Mac 容器**；macOS 级隔离可用 [tart](https://tart.run)/lima 起 microVM 一 job 一 VM。⚠️（未实测）。

## 3. 任务编排 ✅

**「100 用例 → GitHub Actions → 回传」可行流程**：

1. 评测平台调 REST `POST /repos/{org}/{repo}/dispatches`（`repository_dispatch`）带 `client_payload`（评测 ID、用例清单分片、回调地址）。
2. 仓库内一个通用 workflow：`on: repository_dispatch` + `strategy.matrix`（用例 ID 动态来自 payload）；**注意 matrix ≤256**，100 用例单次 run 可行。
3. 每 job `runs-on: [self-hosted, linux, eval]`，runner 拉取用例、在沙箱内执行、结果写 JSON。
4. 回传：job 内 `actions/upload-artifact`；平台轮询 [REST check-runs/check-suite](https://docs.github.com/en/rest/checks/runs) 或 `workflow_job` webhook 跟踪状态，完成后 [Artifacts API](https://docs.github.com/en/rest/actions/artifacts) 下载汇总。⚠️ 需实测 artifact 下载鉴权与速率（REST 有 secondary rate limit，来源：[limits](https://docs.github.com/zh/actions/reference/limits)）。
5. 备选触发：`workflow_dispatch`（带 inputs）或 GitHub App 安装 token；大分片可拆多个 run 绕过 matrix 上限。

## 4. 沙箱隔离（执行不可信代码）⚠️ 多为社区验证

- **SWE-bench 模式**：预构建 Docker 镜像装依赖，容器内跑测试——最低成本基线，但 docker 逃逸面需防（不以 root、禁 --privileged、只读挂载、网络白名单）。
- **专业 sandbox**：E2B、Modal、Daytona 均为 Firecracker microVM 级隔离，API 驱动、秒级启动，适合"一用例一沙箱"；Mac 侧不支持 Firecracker（Linux-only），需远端 Linux 池。来源：[对比](https://fly.io/learn/sprites-vs-e2b-vs-modal-vs-daytona/)。
- **结论**：runner 只当"调度载体"，评测代码一律进沙箱（Docker 起步，microVM 收口）；Mac mini runner 侧重非容器类用例或做控制面。

## 5. 阿里云部署与 CI/CD ⚠️（社区实践，未实测）

- 部署 ECS：Actions 里 `aliyun-cli` 配置 AK + `ssh`/`rsync`/`docker compose`，或构建镜像推 [ACR](https://www.alibabacloud.com/product/container-registry) 后 `aliyun ecs`/`ACK` 滚动更新；参考社区实录。来源：[阿里云 ECS CI/CD 实践](https://www.cnblogs.com/xyyt/p/23377985/github-actions-aliyun-ecs-cicd-pitfalls)。
- DB 迁移：workflow job 内跑 Flyway/Liquibase/Prisma migrate（先备份、加 `concurrency` 组串行、失败门禁阻断部署）。来源：[实践对比](https://plumephp.com/github-actions-database-migration-pipeline/)。

## 6. 可行性边界与演进

| 维度 | 边界 |
|---|---|
| 延迟 | 触发→开跑秒~十秒级（runner 空闲时）；无空闲 runner 则排队，**延迟不可控** |
| 规模 | 并发≤在线 runner 数；matrix≤256/run；排队 24h 上限 |
| 私密性 | 任务 payload 经 GitHub（私有 repo 可见）；计费政策 2026 年内有变风险 |
| 隔离 | 需自建沙箱层，runner 本身无隔离保证 |

**何时演进**：
1. 需要确定低延迟/秒级调度、或并发 >50~100 → **自研 worker + 队列**（SQS/RabbitMQ + worker 拉取），GitHub Actions 退回 CI 本职。
2. 已在 K8s 上且任务批处理化 → **Argo Workflows / Kueue** 做批调度，runner 仅作 K8s 外补充（如 Mac 专属用例）。
3. 短期（评测平台 MVP）：repo 级自托管 runner（Mac mini + 阿里云 Linux）+ repository_dispatch + matrix + artifact 回传即可上线，同时为沙箱隔离与计费变化预留切换面。

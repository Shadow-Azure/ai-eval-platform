# M1-02 · 评测平台原型与全栈设计讨论稿

> 状态：首轮核心决策与运行策略已于 2026-11-08 确认。本文档集把既定架构与现有交互原型展开为可讨论的实现方案，不替代架构定稿，也不宣称完成 M1-03 详细设计验收。
> 架构基线：[评测平台架构方案](../../docs/design/eval-platform-architecture.md)。UI 基线：[prototype/index.html](../../prototype/index.html) 与 [prototype/README.md](../../prototype/README.md)。
> 调研日期：2026-11-08。产品策略已确认；并发、分片和 OIDC 等工程参数保留为实现默认值，可通过技术验证调整。

## 1. 这套文档怎么读

```text
01 诉求与范围（十一条要求如何变成验收边界）
02 原型与交互（页面、状态、用户操作路径）
  ├─► 03 全栈架构（模块边界与关键数据流）
  │     ├─► 04 API 与事件契约
  │     ├─► 05 数据模型与迁移
  │     ├─► 06 调度、并发与资源池
  │     ├─► 07 数据集、指标与 Agent 适配
  │     └─► 08 会话轨迹归一化
  └─► 09 前端实现设计
03/04/05/06/07/08/09 ─► 10 部署、测试与 Review 指南
```

| 文档 | 回答的问题 | 建议读者 |
|---|---|---|
| [01 诉求分析与范围](01-requirements-and-scope.md) | 十一条诉求分别如何落地，MVP 不做什么 | 所有人先看 |
| [02 原型与交互设计](02-prototype-and-interactions.md) | 原型页面、交互、状态与架构如何对应 | 产品与前端 |
| [03 全栈架构设计](03-fullstack-architecture.md) | 前后端、调度器、模型网关、Runner 和存储怎么分工 | 所有人 |
| [04 API 与事件契约](04-api-and-events.md) | 页面和 Runner 用什么接口对话，怎样幂等与断线恢复 | 前后端 |
| [05 数据模型与迁移](05-data-model.md) | 数据怎么存，多租户和重试如何不留隐患 | 后端与 DBA |
| [06 调度、并发与资源池](06-execution-and-resource-pools.md) | GitHub Actions + 阿里云竞价池能否并发、怎样扩缩容和重试 | 后端与运维 |
| [07 数据集、指标与 Agent 适配](07-datasets-metrics-and-agents.md) | 新数据集、新指标、新 Runtime 和 Skill 如何扩展 | 后端与评测开发 |
| [08 会话轨迹归一化](08-trace-normalization.md) | Claude Code、Codex、OpenCode、pi 的主/子 Agent 轨迹怎么统一 | 后端与前端 |
| [09 前端实现设计](09-frontend-implementation.md) | 如何在不偏离原型的前提下落到 React + Ant Design | 前端 |
| [10 部署、测试与 Review 指南](10-deployment-testing-and-review.md) | 合入部署、迁移回滚、验收矩阵和待讨论问题 | Review 时先看 |

## 2. 核心决策摘要

以下是讨论稿建议，不覆盖架构文档中的已定结论。

| 编号 | 决策/默认 | 原因 |
|---|---|---|
| D1 | 单租户阶段仍写入默认 `tenant/workspace/project`，所有查询经过租户作用域 | 避免多租户演进时重写数据访问层 |
| D2 | 登录使用 HttpOnly 安全 Cookie 承载签名会话令牌，服务端保存可撤销会话 | 不把长期令牌放 `localStorage`，支持退出与撤销 |
| D3 | **已确认**：模型密钥只在控制面解密；Runner 使用短期令牌调用平台集中式模型网关 | 网关统一转发、鉴权、监控、配额与成本审计，真实 Key 不进入 Runner |
| D4 | GitHub dispatch 只传 ID 与清单地址，不传完整用例或密钥 | 降低 payload 限制、泄密和 GitHub 元数据耦合 |
| D5 | **已确认**：用例执行状态与解决结果分成两个维度，测试级结果完整上报 | 区分执行失败和答案未通过，并保留每个测试的状态、分组与日志 |
| D6 | SSE 只推送变更通知；DB 快照与事件游标才是恢复依据 | 刷新、重连、服务重启后不能丢历史或让进度倒退 |
| D7 | 回调和文件上传按至少一次投递设计，以事件 ID、内容哈希和唯一约束幂等 | Runner 重试与网络重试无法保证恰好一次 |
| D8 | **已确认**：轨迹以最终原生文件为权威，流式事件只用于实时预览 | 各 Agent 流式事件与最终落盘格式并不等价 |
| D9 | 无法证明父子关系的轨迹进入 `unlinked`，不猜测 subagent 树 | 防止把分支、fork 或普通工具调用误显示为父子 Agent |
| D10 | 指标定义是版本化注册项，聚合器使用平台白名单，不执行数据库中的任意代码 | 支持扩展同时避免远程代码执行与口径漂移 |
| D11 | **已确认**：采用通用 Parallel Change（expand → migrate → contract），应用镜像可回滚但不承诺数据库自动回滚 | 防止回滚旧镜像后读取不兼容 Schema |
| D12 | **已确认**：首期固定单项目、单组织，多租户只保留归属和配额边界，不做租户管理 UI 或复杂角色体系 | 控制 MVP 复杂度，同时保留扩展点 |

## 2.1 首轮已确认的补充边界

- 每次真实执行保存为独立 attempt，旧结果、轨迹和成本不被重试覆盖。
- SWE-bench 主指标采用官方 `% Resolved = resolved / total` 口径；其他数据集通过版本化指标配置选择分子、分母与聚合器。
- 资源供给只使用竞价实例；当前价格高于池阈值时不创建实例，评测保持排队并显示 `resource_price_above_threshold`。
- 主/子 Agent 关联使用 Runtime 专属证据：Claude Code 的 Agent 工具调用/agentId/meta toolUseId、Codex 的 thread spawn edge、OpenCode 的 session parent_id；pi 不把条目树或 fork 误作 subagent。
- 轨迹内容采用分级展示，完整 thinking 与原始文件受权限和审计控制；首轮全部长期保留，不自动清理。
- 结果基线自动推荐且允许手动更换；推荐依据与配置差异必须可见。
- 达到模型预算后评测进入可恢复的预算暂停，用户确认增加预算后从未完成用例继续。
- main 合入后自动迁移和部署；只有应用回滚、破坏性 contract 等高风险操作需要额外审批。

## 3. 评审边界

- 本方案遵从原型现有视觉与主路径，不重做一套 UI。
- 本方案不重新选择技术栈；React、Ant Design、FastAPI、PostgreSQL、Redis、OSS、GitHub Actions 与 ECS Compose 均沿用架构。
- 本方案把“会话元数据入库、原始文件入 OSS”展开为可实现的归一化与降级方案。
- 评审通过后，再将确认内容整理为 M1-03 要求的双语正式详细设计，并拆分实现 issue。

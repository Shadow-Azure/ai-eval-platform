# 架构地图

[English](architecture.en.md) | 中文

当前仓库处于 M3：执行底座已落地，控制面开始承载真实数据集、模型网关与执行运行时，并先用账号密码登录收敛访问。

## 组成

| 区域 | 职责 | 边界 |
|---|---|---|
| `docs/` | 当前事实、开发方法与双语契约 | 不存路线图与决策辩论 |
| `.agents/flow/` | roadmap、milestone、issue 状态机 | 只存流程状态 |
| `scripts/` | 文档与流程机械门禁 | 不承担产品运行时 |
| `.github/workflows/` | 文档、流程、测试与部署的 CI 裁决 | 不代替本地调试 |
| `backend/` | FastAPI 控制面、Worker、迁移与身份/数据集/评测服务 | 不执行 Agent，不保存明文密钥 |
| `web/` | React 控制台：登录、应用壳、评测与运营状态页 | 只经同源 `/api` 访问控制面 |
| `runner-contract/` | Runner 派发、清单、心跳与回传 Schema | 只定义契约 |
| `runner/` | 通用 workflow、合成任务与契约测试 | 不含数据集或 Agent 特例 |

## 文档流水线

```text
Markdown base + English pair + pairing record
                 |
                 v
index -> budgets -> wrap -> pairing -> terminology
                 |
                 v
             warnings on local
                 |
                 v
      links + self-tests in CI
```

中文文件是 `foo.md`，英文侧是 `foo.en.md`，记录是 `foo.i18n.yaml`。记录保存两侧确认时的 blob hash，任何一侧漂移都会让配对门禁失败。

## 身份与会话

账号密码登录；密码以 scrypt 加盐哈希保存，会话 Cookie 是 HttpOnly、SameSite=Lax 的 HMAC 签名令牌，库内只存令牌哈希与撤销状态。`/api/v1/auth/*` 管理会话，`/api/v1/*` 业务路由要求会话，Runner 平面仍用自身短期令牌。默认管理员由 `AUTH_BOOTSTRAP_ADMIN_*` 在 seed 时幂等创建，写操作仅管理员可执行。

## 运营状态

管理员可在 `/operations` 查看控制面 API、PostgreSQL、Redis、对象存储、Runner 平面、调度 Worker 与模型网关的最近探活结果，以及资源池与 Runner、数据集、模型配置、调度队列和数据库迁移版本、大小、关键表行数、连接数与存储容量水位，并可按 PostgreSQL、Redis 与对象存储切换查看。只读成员在导航中不可见且接口返回 403；页面每 30 秒自动刷新，也可手动刷新。Worker 每轮轮询向 `service_heartbeat` 写入心跳。

## Runner 契约

M2 把执行与数据集、Agent 解耦：平台经 repository_dispatch 传 eval_run、shard、attempt、manifest 地址与 digest，Runner 校验后执行，再以心跳、上传清单与完成/失败回调回传。`runner-contract/` 保存版本化 Schema，`runner/` 保存 workflow 模板、合成任务与契约测试。

## 扩展位置

- 产品运行时落地后在本页补组成、数据流与外部依赖。
- 开发任务通过 `.agents/flow/` 推进，流程状态与改动 scope 以该目录为准。
- 平台专属校验在 `scripts/` 增加叶子并由 `run-gates` 组合，同时在 CI 接线。
- 文档量增长先调整层级和预算，再考虑站点投影。

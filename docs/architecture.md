# 架构地图

[English](architecture.en.md) | 中文

当前仓库进入 M6：执行底座与真实评测闭环已落地，产品工作流可用，正在补齐原生轨迹采集与诊断闭环。

## 组成

| 区域 | 职责 | 边界 |
|---|---|---|
| `docs/` | 当前事实、开发方法与双语契约 | 不存路线图与决策辩论 |
| `.agents/flow/` | roadmap、milestone、issue 状态机 | 只存流程状态 |
| `scripts/` | 文档与流程机械门禁 | 不承担产品运行时 |
| `.github/workflows/` | 文档、流程、测试与部署的 CI 裁决 | 不代替本地调试 |
| `backend/` | FastAPI 控制面、Worker、迁移与身份/数据集/评测服务 | 不执行 Agent，不保存明文密钥 |
| `web/` | React 控制台：登录、应用壳、模型凭据、评测与运营状态页 | 只经同源 `/api` 访问控制面 |
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

管理员可在 `/operations` 查看控制面 API、PostgreSQL、Redis、对象存储、Runner 平面、调度 Worker 与模型网关的探活结果，以及资源池、Runner、数据集、模型配置、调度队列与数据库状态，只读成员在导航中不可见；页面每 30 秒自动刷新。

## 模型与凭据

管理员在 `/models` 维护模型配置与加密凭据：列表展示服务商、端点、模型名、API 协议、价格、掩码与最近使用；新增和编辑可绑定已有凭据或写入新 Key；替换 Key、撤销凭据与连通性测试也在同一页完成。写操作仅管理员可执行，普通成员只读；配置变更立即反映到发起评测的模型选择，失效凭据在预检中形成阻塞项。

## Runner 契约

M2 把执行与数据集、Agent 解耦：平台经 repository_dispatch 传 eval_run、shard、attempt、manifest 地址与 digest，Runner 校验后执行，再以心跳、上传清单与完成/失败回调回传。`runner-contract/` 保存版本化 Schema，`runner/` 保存 workflow 模板、合成任务与契约测试。

平台为排队、执行、评分、上传与心跳维护五类超时；取消停止新派发，Worker 对账补齐丢失完成事件。资源连接与竞价池按排队缺口扩缩容，价格超阈值只排队。

竞价池由供给器管理：Worker 解密租户资源连接中的阿里云 AccessKey，查询区域竞价价格与库存，按排队缺口创建 Spot ECS；实例以一次性 JIT 配置注册为 ephemeral Runner 并绑定池标签。价格、实例状态、竞价中断、心跳丢失与创建超时写入资源事件，队列保持可诊断；缩容先注销 Runner 再释放实例。

生产部署把 Runner 回调入口绑定到 VPC 私网地址（`API_BIND_ADDRESS`），安全组按 VPC 网段放行 8000（不新增公网入口），固定 Runner 保留回环路径；`RUNNER_CALLBACK_BASE_URL` 默认由 `API_BIND_ADDRESS` 推导并注入 api 与 worker（可显式覆盖），竞价实例据此取 manifest 并回传结果，并默认从固定端点 `/runner/v1/runner-tarball/<版本>.tar.gz` 下载 actions-runner 包：api 以 OSS 凭据流式读取并要求心跳 Token，地址不随预签名过期，OSS 保持私有；`RUNNER_SPOT_RUNNER_TARBALL_URL` 可显式覆盖为外部镜像。同一发布流水线还会把 `runner/` 与 `runner-contract/schemas/` 打成按 md5 命名的确定性脚本包并缓存到同一私有 OSS，由 `/runner/v1/scripts/<name>` 代理给 Runner；prepare/execute 优先从脚本包运行，只有包不可用时才回退 `actions/checkout`，checkout 不再是主路径。池的 `labels_json.runs_on` 同时决定 Runner 注册标签与分片 dispatch/manifest 的 `runs_on`，自定义标签池因此能接到 execute job；未配置时沿用 `self-hosted,linux,eval`。`/resources` 的供给测试是创建验证，实例在注册 Runner 前释放，完整链路由真实评测验证。

## Agent 资产快照

`/assets` 管理 GitHub 资产源：解析 Ref、识别 Skill（`SKILL.md` 目录）、Command 与 Subagent，并把选中资产冻结为不可变 OSS 包，记录 commit、tree digest、文件摘要与清单。评测引用冻结快照，无资产源则用内置能力；Runner 在隔离 HOME 中映射到 `.claude/`，不重新拉取分支，也不加载仓库或宿主其他资产。Git 令牌加密保存，页面只显示掩码。

## 轨迹采集

Runner 在用例结束时归档 Claude Code 原生文件：主/子 Agent 会话 JSONL、`subagents/*.meta.json` 关系元数据与 stdout/stderr 诊断日志进入 attempt 级多文件清单；失败执行保留文件。每项记录安全逻辑路径、artifact kind、digest、size、content type、Runtime 与格式版本，对象键仍由平台生成。后端校验回调 token 的 attempt 绑定、轨迹对象归属、逻辑路径和 digest 格式，按 object key 合并去重；内容 sha256 校验由后续导入流程完成，旧单文件 `trace_manifest` 继续可读。Runner 发现缺少主会话或子 Agent 元数据时标记采集不完整，后端也不允许仅凭诊断文件声称完整；`degraded` 由后续解析写入，不伪装成完整轨迹。

## 扩展位置

- 产品运行时落地后在本页补组成、数据流与外部依赖。
- 开发任务通过 `.agents/flow/` 推进，流程状态与改动 scope 以该目录为准。
- 平台专属校验在 `scripts/` 增加叶子并由 `run-gates` 组合，同时在 CI 接线。
- 文档量增长先调整层级和预算，再考虑站点投影。

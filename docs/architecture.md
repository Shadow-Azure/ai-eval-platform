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

## GitHub 出口代理

评测容器以 `--network host` 运行，GitHub 出口即 runner 宿主出口；生产在平台 ECS 上以 `deploy/docker-compose.prod.yml` 的 `proxy` 服务运行固定版本 Squid（镜像在 `squid:6.10` 上补装 python3 供 auth helper 使用），同时监听两个端口：3128 是私网监听，不做认证，只接受 RFC1918 来源（`runner_network`，生产收敛到 runner 所在 VPC/子网 CIDR）；3129 是公网监听，要求 HTTP Basic 认证，凭据为平台签发的短期 egress token（放在用户名位、密码留空），供外部 Mac mini 等非 VPC Runner 使用。两个监听共用同一份 GitHub 域名白名单；默认所有池统一走公网 3129：部署流水线从 `ECS_HOST`（公网地址）派生 `RUNNER_EGRESS_PROXY=http://<公网地址>:3129`，每次评测再附带短期 egress token；3128 作为 VPC 内可选直连，绑定 `RUNNER_EGRESS_PROXY_BIND_ADDRESS`（可设为 `API_BIND_ADDRESS`）。3129 默认绑定 0.0.0.0，可用 `RUNNER_EGRESS_PROXY_PUBLIC_BIND_ADDRESS` 收紧；安全组按端口分别限制来源。

API 的 `RUNNER_EGRESS_PROXY` 只接受不含凭据的 http(s) URL，配置后随分片任务 payload 下发，并在同一 payload 里附带 `egress_token`：scope=`egress`，绑定 eval_run/shard/attempt/pool，TTL 与 case token 相同（`max(300, case_timeout_seconds + 900)`），由 `issue_short_token` 用 `RUNNER_TASK_SECRET` 签发；未配置代理时两个字段都不下发，Runner 保持直连。代理侧 `deploy/proxy/egress_auth.py` 用同一个 `RUNNER_TASK_SECRET` 校验 v1 格式的签名、`exp` 与 scope（wire format 与 `backend/app/services/short_token.py` 保持一致），secret 为空时一律拒绝；因此 `RUNNER_TASK_SECRET` 必须同时注入 api 与 proxy 容器。

Runner 仅在 `egress_proxy` 非空时给资产 install 与 Agent 的 `docker exec` 注入 `HTTPS_PROXY`、`HTTP_PROXY` 与 `NO_PROXY`；有 token 时把地址组合成 `http://<token>:@<proxy-host>:<port>`（token 只含 `[A-Za-z0-9._-]`，无需转义），无 token 时沿用不带凭据的地址（私网监听）。未配置时命令参数与直连行为不变。`NO_PROXY` 从 payload 派生平台回调/模型网关主机与 OSS 资产包主机，并固定包含 `localhost`、`127.0.0.1`、`::1`、npm 镜像 `registry.npmmirror.com` 与默认源 `registry.npmjs.org`。

代理只放行 GitHub：设置代理 env 后，非白名单外部请求会被 Squid 直接拒绝（`http_access deny all`），不会回退直连；因此只有确实需要 GitHub 的评测才应配置 `RUNNER_EGRESS_PROXY`，不需要时保持为空、完全不注入。白名单在 `deploy/proxy/squid.conf`：只允许 `github.com`、`api.github.com`、`codeload.github.com`、`objects.githubusercontent.com`、`release-assets.githubusercontent.com`、`github-releases.githubusercontent.com`、`.github.com` 与 `.githubusercontent.com` 的 443 端口，允许/拒绝都写入访问日志。改白名单或 CIDR 后重建 `proxy` 服务。

验证命令（token 取一次真实 payload 的 `egress_token`，或按 v1 格式自签一个过期/错 scope 的 token）：

```sh
curl -x http://<token>:@<proxy-host>:3129 https://api.github.com/rate_limit
curl -x http://<expired-token>:@<proxy-host>:3129 https://api.github.com/rate_limit
curl -x http://<token>:@<proxy-host>:3129 https://registry.npmmirror.com/
docker compose -f deploy/docker-compose.prod.yml logs --tail=100 proxy
```

第一条应返回 GitHub 响应，第二条（过期或伪造 token）应返回 407 认证失败，第三条（非白名单域名）应被拒绝，第四条显示允许/拒绝记录；私网路径把地址换成 `http://<proxy-host>:3128` 且不带 token。

## Agent 资产快照

`/assets` 管理 GitHub 资产源：解析 Ref、识别 Skill（`SKILL.md` 目录）、Command 与 Subagent，并把选中资产冻结为不可变 OSS 包，记录 commit、tree digest、文件摘要与清单。评测引用冻结快照，无资产源则用内置能力；Runner 在隔离 HOME 中映射到 `.claude/`，不重新拉取分支，也不加载仓库或宿主其他资产。Git 令牌加密保存，页面只显示掩码。

## 轨迹采集

Runner 归档会话、元数据与诊断，失败保留；清单记录路径、digest、size、版本与平台对象键。回调写 `trace_file(pending)`；Worker 以 owner 租约领取并校验原件，解析后写 `trace_session`/`trace_span`；`native_session_key` 区分共享 `sessionId`，元数据只留原件行。Claude Code 2.1.278 使用显式版本化 parser：主文件 Agent `tool_use.id`、`toolUseResult.agentId` 与子文件 `.meta.json` 的 `toolUseId` 必须一致才建立父子关系，`parentUuid` 只建 span 层级，同 `sessionId` 或时间相邻不建树；消息按 `message.id` 去重，最终用量取消息快照，成本取 `cost-state`，缺失子文件或元数据进入 degraded/unlinked，孤立 span 保留为 root fragment，大内容只存脱敏截断预览与对象引用。坏行降级，单文件失败隔离。幂等键 `(attempt, sha256, parser_version)`：重复不新增，升级单事务换树，失败保留旧树，单文件修复遇新版本也原子重放整 attempt；回收后旧 Worker 不能续租或完成。`trace_status` 按 failed > incomplete > degraded > ready 聚合，旧 attempt 不覆盖最新；发布时锁 case 行并重读 `trace_file`，inflight 不发布终态，由收尾 Worker 写去重 `trace.ingestion.updated`；Worker SQL 过滤后回填缺 `trace_file` 的 attempt。导入不改评分/终态。控制面按 attempt 提供会话树、span 游标分页、节点详情与大内容地址；tenant、project、case run、attempt 与 session/span 逐层校验，切换 attempt 不会复用旧标识。前端轨迹页把 run、case、attempt、session 与 span 保存到 URL，按 API 父子证据呈现 session tree、waterfall 与 inspector，加载更多 span 时保持游标去重；时钟不可比按 ordinal 回退并提示，unlinked、degraded、failed 与 root fragment 都有独立状态，大内容和 raw 只在用户触发后签发短时地址。只读成员看不到完整 thinking 与敏感属性，原始文件和大内容地址仅管理员可签发并写审计。`eval-shard` 的手动 `trace-e2e` 模式在固定 Linux Runner 上使用真实 Claude Code 2.1.278、确定性 Anthropic-compatible 测试网关、隔离 PostgreSQL 和真实 OSS 对象，验证重试 attempt 隔离、重复回调、parser 重放、降级、raw 下载与审计；常规 PR CI 不执行该外部作业。

## 数据集导入

`/datasets/import` 用三步向导导入数据集：选择适配器并上传 JSON/JSONL 源文件（≤1 GB，拖放），版本标签与数据提交号随文件一起提交；控制面把文件暂存在临时目录，再由带超时与地址空间上限的隔离子进程流式解析、校验并写入持久化报告（识别用例数、字段完整率、逐项错误、重复 case_key）；用户确认后才以单事务生成不可变版本与用例，失败或取消不会留下半成品版本。暂存文件在版本生成、校验失败或取消后删除；进程中断的导入由 Worker 按心跳窗口标记失败，向导不会一直等待。

## 扩展位置

- 产品运行时落地后在本页补组成、数据流与外部依赖。
- 开发任务通过 `.agents/flow/` 推进，流程状态与改动 scope 以该目录为准。
- 平台专属校验在 `scripts/` 增加叶子并由 `run-gates` 组合，同时在 CI 接线。
- 文档量增长先调整层级和预算，再考虑站点投影。

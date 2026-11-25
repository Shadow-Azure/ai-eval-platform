# PR #431 检视记录 — 评测模板机器触发：候选 commit 注入与服务令牌

- **检视对象**：[PR #431](https://github.com/Shadow-Azure/ai-eval-platform/pull/431) `feat(m10): 评测模板机器触发：候选 commit 注入与服务令牌 (#429)`，base `main`，head `agent/candidate-launch-override-03cd0f5d`。
- **提交**：`d77d0bc`（服务令牌与鉴权依赖）、`67e07d6`（launch 覆盖 + 令牌鉴权）、`92bdd45`（文档 §5.14）、`d127276`（空覆盖/凭据错误映射收紧 + 鉴权回归）、`1e7ae4b`（0035 迁移版本断言 + issue scope）。
- **规模**：22 文件，+2784 / −50。
- **owning Note / issue**：[`.agents/flow/issues/m10-candidate-launch-override.md`](../../.agents/flow/issues/m10-candidate-launch-override.md)（GitHub issue #429，状态 `in-progress`）。
- **实现计划**：[`openapi-change.md`](openapi-change.md)（schema 级 diff）、[`docs-5-14-revision.md`](docs-5-14-revision.md)（中英替换文案）；两份均已在 `main` 上，不在本 PR diff 内。
- **日期**：2026-11-25。
- **路径偏离说明**：`pr-review` 技能默认把检视文档落在 `plans/yyyy-mm-dd-pr<N>-review.md`；本 issue 的 `scope` 只允许 `plans/2026-11-25-m10-candidate-launch-override/**`，`plans/2026-11-25-pr431-review.md` 不在白名单内，因此本文件改落当前路径。这样 `verify-flow --diff origin/main` 的 scope 校验仍可通过。
- **检视口径**：本次仅读取代码与证据，未修改任何实现、测试、常驻文档或流程文件；文中的行号均来自对本工作树文件的实际读取，`plans/` 之外的路径指仓库根相对路径。

---

## §0 变更文件树与职责

共 22 = **新增 A 4 + 修改 M 18**，按架构故事排序；每行一句话职责。

```text
# 1) 机器身份：令牌表 + 生命周期服务 + 管理员 API + 鉴权依赖（A3 / M4）
backend/app/models/identity.py                     M  +47   service_token 表模型 + SERVICE_TOKEN_SCOPES 常量
backend/app/models/__init__.py                     M  +4    导出 ServiceToken / SERVICE_TOKEN_SCOPES
backend/migrations/versions/0035_service_token.py  A +102  建 service_token 表 + eval_run 两个 JSON 列 + 索引
backend/app/services/service_tokens.py             A +433  令牌创建/校验/撤销、scope 与白名单、并发/预算上限
backend/app/api/service_tokens.py                  A  +93  POST/GET/DELETE /api/v1/service-tokens（仅管理员会话）
backend/app/api/dependencies.py                    M +103  Actor 主体 + require_session_or_service(scope) 依赖
backend/app/main.py                                M  +11  挂载令牌路由；把三个路由的“路由级鉴权”改为逐路由声明

# 2) 候选覆盖：请求模型、解析与冻结、读模型回显（M4）
backend/app/api/eval_templates.py                  M  +64  AssetOverrideRequest / LaunchLineageRequest；launch 接 Actor、回显 asset/warnings
backend/app/services/eval_templates.py             M +399  覆盖解析（源、commit 断言、快照）、回显与重放、launch 三元返回
backend/app/models/evaluation.py                   M  +10  eval_run.asset_override_json / evolution_lineage_json
backend/app/api/results.py                         M  +27  status/results 换“会话或 eval:read 令牌”并要求回显 asset/lineage

# 3) 契约文档（M5，双语三件套 + 预算登记）
docs/design/eval-platform-architecture.md          M   +6   §5.14 机器触发/令牌鉴权/回显与错误码 + 表 2 两行
docs/design/eval-platform-architecture.en.md       M   +6   英文侧镜像
docs/design/eval-platform-architecture.i18n.yaml   M   +2   重录配对 blob
scripts/doc-budgets.manifest.json                  M   +2   预算 13500 → 13800 并登记理由

# 4) 流程文件（M3）
.agents/flow/issues/m10-candidate-launch-override.md     M +5  status → in-progress；scope 补 5 个实际改动路径
.agents/flow/issues/m10-candidate-launch-override.en.md  M +5  英文侧镜像
.agents/flow/issues/m10-candidate-launch-override.i18n.yaml M +2 重录配对 blob

# 5) 测试（A1 / M3）
tests/backend/test_service_tokens.py               A +590  令牌 CRUD/哈希/撤销/过期/依赖/上限的单测
tests/backend/test_eval_templates.py               M +871  覆盖启动端到端（含 fake GitHub + StubObjectStore）、鉴权回归
tests/backend/test_operations.py                   M   +1  数据库 revision 断言 0034 → 0035
tests/backend/test_deployment_migration.py         M   +1  部署迁移 revision 断言同步
```

- issue scope 里列的 `tests/backend/test_results.py` 本 PR **未改**：status/results 的鉴权与回显用例写进了 `test_eval_templates.py`（`tests/backend/test_eval_templates.py:1922`），不影响 scope 校验。
- `plans/2026-11-25-m10-candidate-launch-override/openapi-change.md` 与 `docs-5-14-revision.md` 是本 issue 的设计产物，已在 `main`，本 PR 只读不改。

---

## §1 一页速览

**一句话**：给“模板一键启动”开了一个机器入口——HE（harness-evolution）拿服务令牌调 launch，可选地把本次评测钉在某个仓库的某个候选 commit 上；平台沿用既有资产解析/快照冻结与 prepare/create 链路，把解析出的 commit 冻结进 run 并在 202/status/results 回显，控制台原路径的输入、返回、权限与幂等语义一律不变。

**一个比喻**：服务令牌是发给自动化的**门禁卡**（卡上写着能进哪几间房、每天能用多少预算），不是“替人登录”；`asset_override` 是一张**取样单**——写明去哪家仓库、取哪个 commit、取哪几份文件，平台当场取样、封存成不可变样本（快照）并贴封条号（`asset_snapshot_id` + `bundle_digest`），之后仓库分支怎么动都不影响这次检测；`commit` 是取样前必须核对的瓶身编号，对不上就整单退回（422），绝不“差不多就行”。幂等键则是单据号——重打同一张单据，拿回的是同一个 run。

### 交付块

| 交付块 | 内容 | 关键位置 |
|---|---|---|
| 机器身份 | `service_token` 表 + 迁移 0035；令牌明文只回一次、库内只存 SHA-256；scope、模板白名单、并发/预算上限、过期与撤销 | `backend/app/models/identity.py:142`、`backend/app/services/service_tokens.py:217`、`backend/migrations/versions/0035_service_token.py:32` |
| 鉴权依赖 | `Actor` 主体 + `require_session_or_service(scope)`：管理员会话或 `eval:launch` 令牌；任意会话或 `eval:read` 令牌；带 `Authorization` 头就只看令牌，不回退 Cookie | `backend/app/api/dependencies.py:103`、`:137` |
| 候选注入 | launch 请求可选 `asset_override`（repo_url/ref/commit/asset_paths/asset_types，整字段替换）与 `lineage`；commit 权威并断言 `resolved_commit == commit` | `backend/app/api/eval_templates.py:90`、`backend/app/services/eval_templates.py:1171` |
| 回显 | 202 带 `asset`（有覆盖时）与 `warnings`（覆盖 + 模板开上报时）；status/results 带 `asset`，results 另带 `lineage` | `backend/app/services/eval_templates.py:1131`、`:1146`、`backend/app/api/results.py:246` |
| 文档 | `docs/design/eval-platform-architecture.md` §5.14 + 表 2 中英镜像；预算 13500 → 13800 | `docs/design/eval-platform-architecture.md:185`、`:197`、`:207`、`:211`、`:231`、`:233` |

### 验证基线（本次实测引用）

| 证据 | 结果 | 来源 |
|---|---|---|
| PR CI | **8/8 全绿**：`backend (pytest + migration)` 8m11s、`backend changes`、`deployment contract`、`docs`、`flow-offline`、`flow-online`、`runner (node contract tests)`、`web (typecheck + build)` | `gh pr checks 431`；runs 45571559222 / 45571559029 / 45571559015 |
| 真实环境冒烟 | 证据 JSON 内 **15 项检查全部 `ok: true`**（交接记录写 14 项，以证据文件为准）：202、`resolved_commit == 请求 commit`（`a82a590ade533b9ce8cc46f9be250e1a726da2d3`）、`requested_ref` 回显、快照与 bundle、幂等重放、status/results 回显、`asset_source_unresolved`/`asset_ref_unresolved` 422、错 scope 403、匿名 401、撤销后 401 | `.tmp-t/smoke-429-evidence.json`（仓库外，未入库） |
| 控制台回归 | 模板列表/详情、评测详情渲染与原型比对（6 张截图 + `facts.json`）；评测详情显示“创建人 `svc:hev-smoke-1681755553`”，模板详情显示启动历史 1 次 | `reports/2026-11-25/m10-429-machine-trigger/`（仓库外，未入库） |
| PR 描述自述 | `pytest` 四套件 127 passed、迁移/运维/鉴权套件全绿、`verify-flow` 与 `doc-quick`/`doc-sync` 绿 | PR body“本地验证”（本次未复跑） |
| 冒烟与门禁段落 | PR body 仍写“（待补：…）” | PR body（见 §6.3） |

> 冒烟与截图证据都在仓库外（`.tmp-t/` 与 `reports/` 未跟踪目录），本 PR 不提交这些产物——符合“证据留档但不入库”的约定。

---

## §2 架构导览

### 2.1 身份层：一个 `Actor` 顶替两种主体

`backend/app/api/dependencies.py:35` 的 `Actor` 是这次新增的“主体抽象”，四个字段说明一切：

```python
@dataclass(frozen=True)
class Actor:
    label: str                      # 控制台 = 用户邮箱；机器 = svc:<令牌名>
    is_admin: bool
    is_service: bool
    scopes: frozenset[str] = frozenset()
    service_token: ServiceToken | None = None
```

`require_session_or_service(scope)`（`dependencies.py:137`）返回一个依赖闭包，内部只有两条路（`:148`）：

- **有 `Authorization` 头 → 只走令牌**（`:153`）。`_service_actor`（`:103`）先拆 `Bearer`、再要求 `hev_` 前缀、再 `authenticate()` 查库、再校验 scope（缺 → 403），最后对 `eval:launch` 校验模板白名单（`:122`，从 `request.path_params["template_id"]` 取，避免依赖路由自己传参）。
- **无头 → 走会话**（`:156`）：`_resolve` 取 Cookie 会话，`eval:launch` 还要求管理员角色（`:160`），`eval:read` 任意已登录角色即可。

两个关键纪律写在模块 docstring（`:3`–`:9`）：**头存在但格式错误永远是 401，绝不悄悄回退到 Cookie**。这条对自动化特别重要——否则一个拼错的令牌会被“恰好还登录着”的浏览器会话兜住，故障被掩盖。`test_service_tokens.py:438` 用“合法 Cookie + `Basic abc`”把这个边界钉住了。

### 2.2 令牌存储：一张只存哈希的表

`backend/app/models/identity.py:145` 的 `ServiceToken`：`token_hash`（64 字符 SHA-256 hex，唯一）、`scopes_json`、`template_allowlist_json`（空 = 全部模板）、`max_concurrent_runs`、`daily_budget_cny`（`Numeric(12,6)`，与平台金额列同精度）、`expires_at`、`revoked_at`、`last_used_at`、`created_by`；唯一约束是 `(tenant_id, name)`，另加 `(tenant_id, id)` 与列表索引。

迁移 `0035_service_token`（`backend/migrations/versions/0035_service_token.py:32`）与模型一一对应：建表 → `ix_service_token_tenant_id`（对应 `tenant_fk()` 的 `index=True`，`backend/app/models/base.py:54`）→ `(tenant_id, revoked_at, created_at)` 复合索引 → 给 `eval_run` 加两列 `asset_override_json` / `evolution_lineage_json`（`server_default '{}'`，老 run 不受影响）→ `downgrade` 逆序删列删表。

比喻：发出去的是**一次性钥匙坯**，锁孔里只留钥匙的齿纹（哈希）；别人捡到数据库也配不出钥匙。

### 2.3 令牌生命周期：创建、认证、上限、视图

`backend/app/services/service_tokens.py` 是纯服务层，错误类自带 `status/code`（`:54`–`:92`），API 层只做转发：

- **创建**（`:217`）：逐字段规范化（`_name`/`_scopes`/`_template_allowlist`/`_max_concurrent_runs`/`_daily_budget`/`_expires_at`），名称先查重再落库，并捕获 `IntegrityError` 兜住并发同名（`:262`）。明文 `hev_` + 32 字节 `token_urlsafe`（`:249`）只在这里返回，不写日志、不进视图。
- **认证**（`:308`）：前缀校验 → 按 `token_hash` 查行 → `_is_active` 判撤销/过期 → 命中后按 60 秒节流刷新 `last_used_at`（`:337`）。节流是为状态轮询设计的：不能让每次 GET 都变成一次写库。
- **上限**（`:343`）：并发按 `created_by == svc:<name>` 且状态非终态计数；预算按当天（UTC 自然日）该主体的 `budget_spent_cny` 求和，`>= cap` 即 429。docstring（`:352`–`:358`）明说这是**准入守卫（best-effort）而非硬配额**，选它而不做强串行化，是因为 prepare/create 链路刻意保持短事务（见 §6.4）。
- **视图**（`:411`）：只回元数据 + `active` 派生位，永不含 `token_hash`；`actor_label`（`:430`）产出 `svc:<name>`，与既有 `created_by` 审计和 `(tenant_id, created_by, idempotency_key)` 幂等约束天然对齐（机器与真人互不串单）。

### 2.4 覆盖解析：先定“去哪个仓库”，再定“取哪个 commit”

`backend/app/services/eval_templates.py` 新增了三个错误类（`:136` `AssetSourceUnresolved`、`:143` `AssetRefUnresolved`、`:150` `CandidateCommitMismatch`，均 422 且 `code` 与 issue 的错误表逐字一致）和一组小函数：

1. `_override_text` / `_override_paths` / `_override_types`（`:1018`–`:1078`）：服务层兜底校验（不信任 Pydantic，因为 service 也被测试/内部直接调用）；`None` 一律表示“该字段缺省 → 继承模板”。
2. `_override_source`（`:1080`）：给了 `repo_url` 就在同租户**精确匹配**已登记 `AgentAssetSource`，未命中 → `asset_source_unresolved`；没给就用模板自己的 `asset_source_id`，模板也没绑 → 同样 `asset_source_unresolved`（“覆盖未指定 repo_url 且模板未绑定资产源”）。
3. `_resolve_override_assets`（`:1171`）：`resolution_ref = commit or ref or 模板 asset_ref or 源 default_ref`（`:1198`）；`ref` 只用于**回显**（`:1209`–`:1216`，有 ref 就原样回显，没有就回显 commit）；随后调用既有 `asset_service.resolve_assets`（`:1224`），把资产域错误分成三档映射（`:1234`–`:1277`）：
   - `asset_path_not_found / asset_selection_empty / asset_type_invalid` → 原码透传（与 run 级资产接口同一契约）；
   - `credential_not_found / credential_invalid` → `asset_ref_unresolved`（凭据坏了等价于对象取不到）；
   - 其余（网络、对象存储）→ `template_reference_invalid`。
4. **断言**（`:1278`）：`commit` 存在且 `snapshot.resolved_commit != commit` → `candidate_commit_mismatch`，details 同时给 `requested` 与 `resolved`。断言发生在 `build_preparation` 之前，所以失败时连 preparation 都不会留下。
5. 归一化身份写进 `effective`（`:1284`），同时作为 202 的 `asset` 块和 run 的冻结值来源。

对照既有路径 `_resolve_template_assets`（`:919`）就能看出设计取向：**老路径一行没改**，新逻辑只是一个分叉（`launch_template:1390`），两条路最终都落到同一个 `build_preparation(asset_snapshot_id=...)`（`:1404`）。也就是说，覆盖只改变“资产从哪来”，不新增任何执行路径。

### 2.5 冻结与回显：run 是唯一事实源

`launch_template`（`:1321`）现在返回 `(run, created, launch_meta)`，并在三处落库：

- `run.asset_override_json = {"requested": ..., "effective": ...}`（`:1456`）：既留原始请求（审计：调用方到底要了什么），也留归一化结果（回显：实际测了什么）；
- `run.evolution_lineage_json = lineage`（`:1463`）：纯血缘，不参与评测语义；
- `launch_meta` 只带 `asset` 与 `warnings`，由 API 层决定是否放进 202（`backend/app/api/eval_templates.py:315`）。

读侧 `run_asset_identity`（`:1146`）刻意**读快照而不是读模板**：`resolved_commit` 取 `AgentAssetSnapshot.resolved_commit`，`asset_snapshot_id` 取快照 id，`requested_ref` 优先取冻结的 `effective.requested_ref`（覆盖启动）否则取 `snapshot.requested_ref`（控制台启动）。于是 status/results 的回显永远等于“当时测的那一份”，模板改名、分支移动、令牌轮换都改不了它。

### 2.6 错误信封与装配

- API 层统一 `_http_error`（`backend/app/api/eval_templates.py:122`）：把 `field` 折成 `details.fields.<field> = "invalid"`，再交给 `error_detail`（`backend/app/api/errors.py:8`）拼 `{"error": {code, message, request_id, details}}`。模板错误与令牌错误共用这一个函数。
- `main.py:79`–`:95` 把 `eval_templates` 与 `results` 两个路由的**路由级** `Depends(require_session)` 摘掉，改为逐路由声明：launch 用 `require_session_or_service("eval:launch")`，status/results 用 `eval:read`，compare/case-tests 保留 `require_session`，模板列表/详情保留 `require_session`，写操作保留 `require_admin`。这类“拆路由级依赖”的改动最容易漏鉴权，PR 用 `test_routes_that_dropped_router_dependencies_require_auth`（`tests/backend/test_eval_templates.py:1503`）逐个路径断言 401——这是本次检视里我最认可的一处防御。

---

## §3 数据流走查

下面用一次真实的机器启动（数据取自冒烟证据，模板 `1aac08bc…`，请求 commit `a82a590ade533b9ce8cc46f9be250e1a726da2d3`）走完整条链路，每步给锚点。

### 剧本 A：带覆盖的机器启动（成功路径）

1. **管理员建令牌**（控制台会话）→ `POST /api/v1/service-tokens`（`backend/app/api/service_tokens.py:48`）→ `_admin` 依赖校验管理员 → `create_token` 生成 `hev_…` 并只存哈希（`service_tokens.py:249`）→ 201 响应里 `token` 只出现这一次（响应里 `response.text.count(plaintext) == 1`，`tests/backend/test_service_tokens.py:148`）。
2. **HE 调 launch**：`Authorization: Bearer hev_…` + `Idempotency-Key` + body（示例）：

   ```json
   {
     "idempotency_key": "round-1-candidate",
     "asset_override": {
       "repo_url": "https://github.com/Shadow-Azure/ai-eval-platform",
       "ref": "main",
       "commit": "a82a590ade533b9ce8cc46f9be250e1a726da2d3",
       "asset_paths": [".claude/skills/review/SKILL.md"],
       "asset_types": {".claude/skills/review/SKILL.md": "skill"}
     },
     "lineage": {"evolutionRunId": "evo-smoke-429", "evolutionRoundId": "round-1", "roundSeq": 1}
   }
   ```

3. **鉴权**（`dependencies.py:153` → `_service_actor`）：拆头、查令牌、验 `eval:launch`、验模板白名单 → `Actor(label="svc:<name>")`。
4. **route 归一化请求**（`backend/app/api/eval_templates.py:276`）：`model_dump(exclude_unset=True)` 让“没传”和“显式 null”都表现为缺省；`created_by=context.label`（`:291`），因此 run 的 `created_by` 落 `svc:<name>`。
5. **服务层**（`services/eval_templates.py:1347` 起）：空覆盖归一（`:1352`）→ 幂等重放优先（`:1364`–`:1378`）→ 令牌上限准入（`:1379`，先于“模板是否启用”）→ `enabled` 校验（`:1381`）→ 自进化上报目标校验（`:1386`，坏目标不产生 run）→ 覆盖解析并断言 commit（`:1395` → `:1171`）→ `resolve_assets` 冻结快照（可能复用同 digest 的既有快照）。
6. **prepare/create**（`:1404`、`:1442`）：把 `asset_snapshot_id` 交给既有 `build_preparation`，`consume_preparation` 建 run（`backend/app/services/preparations.py:1027` 把快照 id 写到 run）。随后补 `template_id/version/snapshot`、写 `asset_override_json/lineage`、`launch_count += 1`、一次 `commit`（`:1453`–`:1469`）。
7. **202**（`api/eval_templates.py:302`）：`{run_id, template_id, status, status_url, result_url, created, asset:{repo_url, requested_ref, requested_commit, resolved_commit, asset_snapshot_id, bundle_digest}}`；本场景模板未开上报，故无 `warnings`。
8. **HE 比对**：把 `asset.resolved_commit` 与请求 commit 逐字比较（平台已断言过一次，这里是双保险）；不一致就说明请求和响应被中间层改写，HE 不进入轮询。
9. **轮询 status**（`api/results.py:257`）：用 `eval:read` 令牌读；响应除既有进度字段外多一个 `asset{resolved_commit, asset_snapshot_id, requested_ref}`（`:308`–`:311`），取自快照（`services/eval_templates.py:1146`）。
10. **取 results**（`api/results.py:177`）：终态 `is_final=true`，附 `asset` 与 `lineage`（`:246`–`:253`）；HE 据此换算目标分/覆盖率/护栏（裁决在 HE，平台不给 verdict）。

### 剧本 B：幂等重放

HE 超时重发同一 `Idempotency-Key`：`launch_template` 先查 `(tenant, created_by, key)`（`:1364`），命中且模板一致 → `(existing, False, _replay_launch_meta(...))`。`_replay_launch_meta`（`:1309`）从 run 冻结的 `effective` 重建 `asset`，**不重新解析资产、不重新扣上限**（重放先于上限检查）。所以网络重试不会失败、也不会多建 run（冒烟证据 `idempotent_replay created=False` 即此）。注意重放的 `warnings` 是用**当前**模板重算的（见 §6.6）。

### 剧本 C：失败路径（都不创建 run）

| 场景 | 触发点 | 返回 |
|---|---|---|
| 没带令牌 / 令牌无效、撤销、过期 / 头格式错 | `dependencies.py:111`–`:118` | 401 `unauthenticated` |
| 令牌 scope 不足 / 模板不在白名单 | `dependencies.py:120`–`:127` | 403 `forbidden` |
| `repo_url` 未登记 | `services/eval_templates.py:1102` | 422 `asset_source_unresolved`（details 带 repo_url） |
| commit/ref 在仓库取不到、凭据失效/无效 | `:1242`、`:1260` | 422 `asset_ref_unresolved`（details 带 requested_ref + 子码） |
| 解析出的 commit 与请求不一致 | `:1278` | 422 `candidate_commit_mismatch`（details 带 requested/resolved） |
| 覆盖后选择为空 / 路径不存在 | `:1235` | 422 `asset_selection_empty` / `asset_path_not_found`（复用资产域码） |
| 并发/预算到顶 | `dependencies` → `enforce_launch_caps`（`service_tokens.py:379`、`:400`） | 429 `service_token_limit_exceeded`（details 带 cap/active 或 cap/spent） |

所有失败都发生在 `consume_preparation` 之前或之中，冒烟与单测都用 `_run_count(...) == 0` 反证“没有半创建的 run”（如 `tests/backend/test_eval_templates.py:1683`）。

### 剧本 D：控制台一键启动（回归路径）

无 `Authorization`、无 `asset_override`：会话鉴权 → 管理员角色 → `asset_override=None` → 走**未改动的** `_resolve_template_assets`（`:1391`）→ `launch_meta = {"asset": None, "warnings": []}` → 202 里**没有** `asset`/`warnings`（`api/eval_templates.py:315`，有值才加键）。run 的 `asset_override_json` 保持 `{}`。但 status/results 仍会回显 `asset`（任何冻结过快照的 run 都回显，见 §6.7）——这是**增量字段**，控制台不读它，旧 run 也不因缺失该字段改变其它行为。

---

## §4 导读地图

| 层 | 文件 | 状态 | 说明 |
|---|---|---|---|
| 流程 | `.agents/flow/issues/m10-candidate-launch-override.md` | ✅ | 通读；验收 7 条是 §5 的来源 |
| 流程 | `.agents/flow/issues/m10-candidate-launch-override.en.md` | ✅ | 仅 diff（与中文镜像一致） |
| 流程 | `.agents/flow/issues/m10-candidate-launch-override.i18n.yaml` | ✅ | 仅 diff（两个 blob 重录） |
| 计划 | `plans/…/openapi-change.md`、`docs-5-14-revision.md` | ✅ | 已在 main，作为契约对照 |
| 鉴权 | `backend/app/api/dependencies.py` | ✅ | 全文 |
| 身份 | `backend/app/models/identity.py` | ✅ | 变更区（142–183） |
| 身份 | `backend/app/models/__init__.py` | ✅ | 仅 diff（导出） |
| 身份 | `backend/migrations/versions/0035_service_token.py` | ✅ | 全文 |
| 身份 | `backend/app/services/service_tokens.py` | ✅ | 全文 |
| 身份 | `backend/app/api/service_tokens.py` | ✅ | 全文 |
| 装配 | `backend/app/main.py` | ✅ | 变更区（74–98） |
| 覆盖 | `backend/app/api/eval_templates.py` | ✅ | 变更区 + launch 全函数（90–134、262–320） |
| 覆盖 | `backend/app/services/eval_templates.py` | ✅ | 新增段 1018–1470 逐行读；旧路径 919–948 对照读 |
| 覆盖 | `backend/app/models/evaluation.py` | ✅ | 变更区（155–168） |
| 回显 | `backend/app/api/results.py` | ✅ | 变更区（176–317）+ `_run_or_404` |
| 文档 | `docs/design/eval-platform-architecture.md` | ✅ | diff + 命中段逐行 |
| 文档 | `docs/design/eval-platform-architecture.en.md` | ✅ | diff（逐句对照中文） |
| 文档 | `docs/design/eval-platform-architecture.i18n.yaml` | ✅ | 仅 diff |
| 文档 | `scripts/doc-budgets.manifest.json` | ✅ | 仅 diff |
| 测试 | `tests/backend/test_service_tokens.py` | ✅ | 全文（1–589） |
| 测试 | `tests/backend/test_eval_templates.py` | ✅/◻ | 新增段（1129–1991）与 helper 全读；**既有 1–1128 行未逐行重读**（标 ◻） |
| 测试 | `tests/backend/test_operations.py` | ✅ | 仅 diff（revision 断言） |
| 测试 | `tests/backend/test_deployment_migration.py` | ✅ | 仅 diff（revision 断言） |

---

## §5 人工验收清单

来源：issue #429「验收」7 条 + PR 描述「本地验证/冒烟/门禁」。`[x]` 已有本次实测证据；`[ ]` 仍需人工拍板或复跑。

- [x] 带覆盖 launch 一个真实候选 commit：202 的 `asset.resolved_commit` 逐字等于请求 commit，status/results 回显一致。证据：冒烟 `resolved_commit_equals_request / status_asset_echo`（commit `a82a590…`）；单测 `tests/backend/test_eval_templates.py:1328`。
- [x] golden 注入证明：候选改一个 `SKILL.md`，快照 manifest 中该文件 digest 与 commit 中对应 blob 一致。证据：单测 `test_eval_templates.py:1686`（`sha256(SKILL_CONTENT)` 对齐 manifest，再与 fake tree/blob 对照）。
- [x] commit 权威：同时给 ref 与 commit 按 commit 解析、ref 原样回显；仅给不可解析 commit → 422 `asset_ref_unresolved` 且不创建 run。证据：单测 `:1476`（断言 provider 只被问了 commit）、`:1573`；冒烟 `unresolvable_commit_422`。
- [x] 跨仓注入：未登记的 `repo_url` → 422 `asset_source_unresolved`；登记后同一请求成功。证据：单测 `:1540`；冒烟 `unregistered_repo_422`。
- [x] 覆盖后选择为空 / 路径不存在 → 422 对应码，不建 run。证据：单测 `:1626`。
- [x] 不带 `asset_override` 的控制台一键启动行为不变：202 无 `asset` 块，权限/幂等/校验语义不变。证据：单测 `:1412`、`:1455`、`:473`；控制台截图与原型比对（模板列表/详情、评测详情）。
- [x] 服务令牌：无令牌 → 401（按实现口径，issue 原文写 403，见 §6.2）、错 scope / 模板不在白名单 → 403、`created_by=svc:<name>`、幂等重放同一 run。证据：单测 `:1767`、`:1802`；冒烟 `wrong_scope_403`、`anonymous_read_401`、`revoked_token_401`、`machine_actor_recorded`；`facts.json` 中评测详情“创建人 `svc:hev-smoke-1681755553`”。
- [x] `npm test`、`verify:flow`、`lint:docs`、`verify:docs` 与 PR CI 通过。证据：CI `docs`、`flow-offline`、`flow-online`、`backend (pytest + migration)` 全绿（`gh pr checks 431`）；本地套件按 PR 描述为绿，本次未复跑。
- [ ] 429（并发/预算上限）在真实环境的返回体与文档一致性：单测覆盖（`test_eval_templates.py:1864`、`:1896`），但冒烟未走 429 分支，且常驻文档尚未记录该码（见 §6.3）。
- [ ] PR body 的“冒烟测试/门禁”两个「待补」段落回填：证据已存在，但描述未更新（见 §6.3）。

---

## §6 讨论点

> 每条固定五段：**位置 / 现象 / 问题 / 预判 / 结论**。结论栏留空给拍板；预判只是初判，不是定论。

### 6.1 `repo_url` 精确匹配在多资产源时可能选错源

- **位置**：`backend/app/services/eval_templates.py:1093`–`:1108`；`backend/app/models/agent_asset.py:36`、`:62`。
- **现象**：覆盖给了 `repo_url` 时，按 `(tenant_id, repo_url)` 查 `AgentAssetSource`，`order_by(created_at, id)` 取**第一条**。而 `AgentAssetSource` 只保证 `name` 唯一，`repo_url` 没有唯一约束——同一仓库可以登记多个源（不同凭据、不同 `scan_paths`、不同 `default_ref`）。
- **问题**：模板本身明明绑定了某个源（含它的凭据与扫描路径），但覆盖只传了同一个 `repo_url` 时，平台会静默改用“最早登记的那个源”，而不是模板自己的源。对“同一 repo 两个源、凭据权限不同”的现场，解析结果可能不是调用方预期的那个；且没有任何提示。
- **预判**：不是本 PR 的功能缺陷（issue 只要求“按 repo_url 匹配已登记源”），但属于可低成本收紧的健壮性问题。建议：若 `template.asset_source_id` 对应的源 `repo_url` 与请求相等，**优先用它**；否则再退回“最早登记”。或者退一步，在 `_override_source` 命中多条时把 `source_id` 记进审计（现在 `effective.source_id` 已经有，但调用方看不到）。
- **结论**：2026-11-25 已修：`_override_source` 命中多个源时优先模板自身的源；仍有歧义则 422 `asset_source_unresolved` 并在 `details.candidates` 返回候选，不再静默取最早。新增两条用例（`test_override_prefers_the_template_source_when_repo_url_has_candidates`、`test_override_rejects_ambiguous_repo_url_without_a_template_source`），提交 `74c4eff`。

### 6.2 缺凭据返回 401 而非 issue 验收写的 403

- **位置**：`backend/app/api/dependencies.py:46`–`:57`、`:117`–`:127`；issue 验收原文 `.agents/flow/issues/m10-candidate-launch-override.md:175`（“无令牌/错 scope/模板不在白名单 → 403”）。
- **现象**：实现把“没令牌、令牌格式错、无效、撤销、过期”统一映射为 **401 `unauthenticated`**；“scope 不足、模板不在白名单”映射为 **403 `forbidden`**。冒烟证据也是 `anonymous_read_401` + `wrong_scope_403`。
- **问题**：与 issue 验收文字不一致。若只看验收单，会以为“无令牌 = 403”。好处是 401/403 的语义分工更清晰（认证失败 vs 授权不足），也与平台既有会话语义一致（`require_session` 一直返回 401）。
- **预判**：**保留 401**（改实现会把“认证 vs 授权”混回一锅），但应当把 issue 验收文字改成与新口径一致，并在常驻文档补一句“401 表示未认证或令牌无效/已撤销/已过期”。这属于“文字对齐”，不是代码修复。
- **结论**：2026-11-25 保留 401；issue 验收改为“无有效凭据 → 401；scope/白名单不足 → 403；达到并发/预算上限 → 429”，§5.14 与 `openapi-change.md` 同步，提交 `74c4eff`。

### 6.3 调用方契约缺口：429 / 401 与 OpenAPI 的 bearer scheme 没进文档

- **位置**：`docs/design/eval-platform-architecture.md:197`、`:207`；`backend/app/services/service_tokens.py:90`–`:92`；`plans/2026-11-25-m10-candidate-launch-override/openapi-change.md:169`–`:186`；`backend/app/api/dependencies.py:137`–`:168`。
- **现象**：三段现象叠加——
  1. 常驻文档写了“并发/预算上限”，但没写**超限返回 429 `service_token_limit_exceeded`**（grep 全文无 `429`），也没写 launch/status/results 新增的 **401**；
  2. 设计稿 `openapi-change.md` 的 `LaunchErrorCodes` 枚举同样只有 403/404/409/422，没有 429（该稿定稿早于“上限用 429”的实现决定）；
  3. 实现用普通 `Depends` 而不是 `Security`/`HTTPBearer`，`securitySchemes.serviceToken`（计划 §1 明确要求）不会出现在运行时生成的 OpenAPI 里。
- **问题**：HE 是外部集成方，读的是文档与 OpenAPI。429 是它必须处理的**可重试/需退避**信号，401 是它刷新令牌的信号；这两个码没写进文档，会让适配器把“限额”当未知错误。Bearer scheme 缺失则让“生成的 OpenAPI”无法表达机器鉴权。
- **预判**：429/401 段落在本 PR 或紧跟的一个 `docs:` 小 PR 补（§5.14 加两句即可，预算已到 13800，建议压缩别处或按门禁登记）；`openapi-change.md` 是历史设计稿，可不回改，但要接受“archive 与实现有偏差”。OpenAPI securityScheme 属“要不要对外暴露机器鉴权”的产品决定，本 PR 不做也说得过去，建议登记为后续项。**PR body 的“冒烟/门禁待补”也建议一并回填**——证据已在仓库外留档，描述里还是“待补”，会让合并记录与事实脱节。
- **结论**：2026-11-25 已补：§5.14 增加 401/403/429、best-effort、回显范围与令牌名复用；`openapi-change.md` 的枚举与触发条件同步 429/401；issue 验收同步；PR body 已回填冒烟与 CI（截图见 release `m10-429-machine-trigger-evidence-20261125`）。提交 `74c4eff`。`securitySchemes` 仍缺，登记为后续 issue（HE 以文档适配，不依赖生成式 schema）。

### 6.4 上限是 best-effort；令牌名一旦撤销就永久占用

- **位置**：`backend/app/services/service_tokens.py:343`–`:408`（上限）、`:240`–`:247`（名称查重不过滤 revoked）；`backend/app/services/eval_templates.py:1379`。
- **现象**：并发/预算在“计数 → 建 run”之间没有锁（docstring `:352` 明说 best-effort）；预算只统计已结算到 `budget_spent_cny` 的 run，所以同一毫秒的多个 launch 可以一起越过上限。另一方面，`create_token` 按 `(tenant, name)` 查重时**包含已撤销的行**，撤销 `he-bot` 后不能再建同名令牌，只能换名——换名会让 `created_by` 从 `svc:he-bot` 变成 `svc:he-bot-2`，历史 run 的归属在审计视角下“断代”。
- **问题**：两个都是“可接受但要知道”的取舍：上限是准入守卫而不是账单；名称是令牌的**永久标识**（好处是 `svc:<name>` 不会被复用冒充，坏处是运维得接受改名）。
- **预判**：上限语义按 docstring 接受，但建议在常驻文档补一句“上限为 best-effort 准入守卫，不是硬配额；预算按已结算花费统计”。名称永久占用也值得写成运维事实（或提供“允许复用已撤销名称”的显式开关，由管理员确认——但那会削弱审计可读性，不建议首期做）。另外 `enforce_launch_caps` 排在 `template.enabled` 之前（`:1379` vs `:1381`），所以“上限已满 + 模板停用”会先返回 429 而不是 409；无实害，但如果希望错误优先级稳定，可把 enabled 校验提前。
- **结论**：2026-11-25 接受：上限维持 best-effort（预算按已结算花费、UTC 自然日），§5.14 已写明；`create_token` docstring 与 `openapi-change.md` 写明“名称租户内唯一且撤销后不复用，保证 `svc:<name>` 审计稳定”；错误优先级不变（429 先于 409，无实害）。提交 `74c4eff`。

### 6.5 空覆盖的边界：`{}` 等于“没意图”，`{"asset_paths": []}` 等于“就要空选择”

- **位置**：`backend/app/services/eval_templates.py:1349`–`:1355`、`:1217`–`:1222`；测试 `tests/backend/test_eval_templates.py:1455`、`:1647`。
- **现象**：`launch_template` 用 `not any(value is not None ...)` 判断“空覆盖”，所以 `{}`、`{"ref": null}`、`{"commit": null, "repo_url": null}` 都退化为控制台语义（不要求模板绑源、202 无 `asset`）；但 `{"asset_paths": []}` 因为值是 `[]`（非 None）被当作**有意图**，整字段替换成空列表，最终 422 `asset_selection_empty`。
- **问题**：两种“看起来都像空请求”的输入行为完全不同。这是“整字段替换”语义的正确推论（显式空列表就是“什么都不要”），但对 HE 的适配器是个坑：序列化时把 `None` 写成 `[]` 或把 `[]` 写成 `None`，结果一个是“继承模板”一个是“422”。
- **预判**：实现正确，不建议改语义；建议在文档/接口示例里给一组对照（`{}` = 一键启动、`{"asset_paths": []}` = 选择为空被拒），并在 HE 适配器里对“未设置”统一省略字段而不是发 `null`/`[]`。
- **结论**：2026-11-25 接受：语义不变（`{}`/全 null 按无覆盖，显式空列表按整字段替换并 422 `asset_selection_empty`）；HE 适配器应省略未设置字段。§5.14 未逐例展开，示例留给 OpenAPI 适配稿。

### 6.6 重放的 `warnings` 用“当前模板”重算，不是冻结值

- **位置**：`backend/app/services/eval_templates.py:1309`–`:1318`、`:1298`–`:1306`。
- **现象**：重放时不重新解析资产（正确），但 `warnings` 由 `_override_warnings(template)` 按**此刻**的模板 `self_evolution_enabled` 重新生成。于是：启动时模板未开上报（202 无 warnings）→ 模板后来开启上报 → 同一个幂等键重放，202 突然多出 `warnings`。
- **问题**：严格幂等通常意味着“同一请求同一响应”。这里多出的只是一条**非阻断提示**，不会改变 run 或执行语义；但确实让“重放响应逐字节一致”不再成立。
- **预判**：无实害，可接受。若以后要求严格幂等，可把 `warnings` 一并冻结进 `asset_override_json`（或 run 的另一个 JSON 列），重放直接回放。当前不建议为它扩列。
- **结论**：2026-11-25 接受（无实害）：重放提示按当前模板重算，不扩冻结列；已在 §7 留档。

### 6.7 `asset` 回显范围扩大到“所有冻结过快照的 run”（additive）

- **位置**：`backend/app/services/eval_templates.py:1146`–`:1168`；`backend/app/api/results.py:246`–`:254`、`:308`–`:311`；测试 `tests/backend/test_eval_templates.py:1437`。
- **现象**：`run_asset_identity` 只看 `run.asset_snapshot_id`，不区分“覆盖启动 / 控制台模板启动 / run 级资产评测”。因此控制台一键启动的 run、以及既有的 `POST /api/v1/evaluations` 资产评测，status/results 也会多出 `asset`（而它们的 202 没有 `asset`，这是有意的）。
- **问题**：字段是**纯增量**，控制台不读，旧 run 也不因此改变其它行为。唯一“不一致”是：控制台 run 的 202 没有 `asset`，但 status 有——这不是同一契约内的矛盾（202 只对带覆盖的调用方承诺 `asset`），但对读代码的人是理解成本。
- **预判**：接受。建议在文档 §5.14 把“回显范围”写成一句话（任何冻结过资产快照的 run 都会回显），省得后人以为只有覆盖启动才有。
- **结论**：2026-11-25 接受：§5.14 已写明“任何冻结过资产快照的 run 都在 status 与 results 回显 `asset`”，`openapi-change.md` 兼容性段同步。

### 6.8 控制台文案仍写“启动请求不能覆盖固定字段”（`web/` 不在 scope）

- **位置**：`web/src/pages/EvalTemplateDetailPage.tsx:439`、`web/src/components/TemplateApiModal.tsx:111`；`facts.json` 的模板详情文本里能看到同一句在页面上真实渲染。
- **现象**：机器启动已经可以覆盖资产选择，但控制台“边界”说明还写“启动请求不能覆盖固定字段”。对控制台用户而言这句话仍基本成立（UI 没有覆盖入口），对读文档/API 的机器调用者则是过期信息。
- **问题**：issue #429 的 scope 明确不含 `web/`，所以本 PR 不改是对的；但这条文案迟早要改，否则“文档说能覆盖、控制台说不能覆盖”会同时存在。
- **预判**：开一个 `web` 小 PR 改成“控制台一键启动不可覆盖；机器触发（服务令牌）可携带候选覆盖”，并顺带在“接口”弹窗里补 `/api/v1/service-tokens` 的存在。不建议塞进本 PR（会破坏 scope 门禁的一致性）。
- **结论**：2026-11-25 记录为后续 `web` 小 PR；本 PR 不改 `web/`。

---

## §7 分流表

| 发现 | 定性 | 去处 | 状态 |
|---|---|---|---|
| 401（未认证/令牌无效）取代 issue 验收写的 403 | 文字对齐，非 bug | 更新 issue #429 验收措辞 + §5.14/openapi 补 401 一句 | ✅ 已决（2026-11-25，issue 与文档已同步，`74c4eff`） |
| 429 `service_token_limit_exceeded` 未进常驻文档与设计稿枚举 | 文档缺口 | §5.14 与 `openapi-change.md` 补 429/401 | ✅ 已决（2026-11-25，`74c4eff`；预算 13800 → 13900） |
| 上限 best-effort、预算按已结算花费 | 已知取舍（docstring 已写） | 常驻文档补一句语义说明，理由留在本检视文档 | ✅ 已决（2026-11-25，§5.14 已写明，`74c4eff`） |
| 撤销后令牌名不可复用（`svc:` 标签断代） | 可接受的运维事实 | 文档与 `create_token` docstring 写明；不建重复 Note | ✅ 已决（2026-11-25，`74c4eff`） |
| `repo_url` 多条匹配时静默取最早源 | 健壮性收紧 | 优先模板源；仍歧义则 422 返回 candidates | ✅ 已修（2026-11-25，两条用例，`74c4eff`） |
| 重放 `warnings` 按当前模板重算 | 无实害的幂等瑕疵 | 本检视文档留档；将来重构时冻结 | ✅ 已决（2026-11-25，接受） |
| `asset` 回显覆盖所有冻结 run | 纯增量、有意为之 | §5.14 与 openapi 兼容性段补“回显范围” | ✅ 已决（2026-11-25，`74c4eff`） |
| OpenAPI 未声明 `serviceToken` securityScheme | 契约完整性问题 | 后续 issue（HE 以文档适配，不依赖生成式 schema） | ◻ 后续 issue（2026-11-25 拍板） |
| 控制台“不能覆盖固定字段”文案过期 | 文档/UI 债 | 单独 `web` 小 PR（`web/` 不在 #429 scope） | ◻ 后续 web issue（2026-11-25 记录） |
| 冒烟证据在仓库外、未入库；PR body 仍写“待补” | 过程记录 | 回填 PR 描述；证据不入库 | ✅ 已办（2026-11-25，PR body 已回填；截图在 release `m10-429-machine-trigger-evidence-20261125`） |

---

## §8 链接

- PR：[#431](https://github.com/Shadow-Azure/ai-eval-platform/pull/431)
- Issue / owning 工作文件：[#429](https://github.com/Shadow-Azure/ai-eval-platform/issues/429) → [`.agents/flow/issues/m10-candidate-launch-override.md`](../../.agents/flow/issues/m10-candidate-launch-override.md)
- 实现计划：[`openapi-change.md`](openapi-change.md)（schema diff）、[`docs-5-14-revision.md`](docs-5-14-revision.md)（文档替换稿）
- 常驻文档：[`docs/design/eval-platform-architecture.md`](../../docs/design/eval-platform-architecture.md) §5.14（第 185/197/207/211 行）与表 2（第 231/233 行）
- 既有覆盖管道证据：`backend/app/api/evaluations.py`、`backend/app/services/agent_assets.py:2001`
- 相关 issue：#378（评测模板）、#382（模板级自进化上报）、#410（模板来源展示）
- 冒烟证据（仓库外，未入库）：`.tmp-t/smoke-429-evidence.json`；截图与事实：`reports/2026-11-25/m10-429-machine-trigger/`（`01`–`06` PNG、`facts.json`、`smoke-report.html`）
- 后续 PR：控制台文案修正（`web/`，待开）

---

## §9 文件级代码分析

### 9.1 `backend/app/models/identity.py`（M +47）

- **职责**：新增 `service_token` ORM 模型与 scope 常量；变更形态 M（在 `AuthSession` 之后插入）。
- **关键依赖**：SQLAlchemy 2.x `Mapped`/`mapped_column`——仓库统一的数据层写法；`json_type()`（`backend/app/models/base.py:32`）让 JSON 列在 PostgreSQL 用 JSONB、测试用 SQLite 时用 JSON，这是本仓库“同 schema 双库”的惯用缝。
- **变更代码块**：

  ```python
  # 旧：AuthSession 之后直接是 AuditLog
  # 新：
  SERVICE_TOKEN_SCOPES = ("eval:launch", "eval:read")

  class ServiceToken(Base, TimestampMixin):
      __tablename__ = "service_token"
      token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
      template_allowlist_json: Mapped[list] = mapped_column(json_type(), default=list, ...)
      max_concurrent_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
      daily_budget_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
      ...
      __table_args__ = (
          UniqueConstraint("tenant_id", "name", name="uq_service_token_tenant_name"),
          UniqueConstraint("tenant_id", "id", name="uq_service_token_tenant_id_id"),
          Index("ix_service_token_tenant_revoked_created", "tenant_id", "revoked_at", "created_at"),
      )
  ```

  为什么这么写：`(tenant_id, id)` 复合唯一是仓库里所有租户内实体的既有惯例（供跨表复合外键引用）；`(tenant_id, revoked_at, created_at)` 索引服务于“列出租户令牌（按撤销状态与创建时间）”的查询形状；金额用 `Numeric(12,6)` 与 `eval_run.budget_spent_cny` 同精度，避免比较时出现浮点尾差。
- **实现思路**（≤3 行）：令牌只是“带权限范围的主体档案”，密钥材料刻意不在模型里留明文位；上限与白名单作为列而不是独立表，省掉一次 join。
- **检视发现**：无功能发现。唯一可提的是 `scope` 常量放在 `identity.py` 而不是 `services/service_tokens.py`，导致 `dependencies.py` 与 service 都从 `models` 导入业务常量——分层上略怪，但避免了 models ←→ services 的循环导入，可接受。

### 9.2 `backend/app/models/__init__.py`（M +4）

- **职责**：导出 `ServiceToken`、`SERVICE_TOKEN_SCOPES`（`:42`、`:47`、`:96`–`:97`）；变更形态 M，纯增量。
- **关键依赖**：无第三方；仓库用 `__init__` 做统一模型入口，所有服务层 `from ..models import X`。
- **变更代码块**：`from .identity import (` 里加两行 + `__all__` 加两行。为什么这么写：保持“模型从包根导入”的既有约定，服务层不用深入子模块。
- **实现思路**：无逻辑。
- **检视发现**：无。

### 9.3 `backend/migrations/versions/0035_service_token.py`（A +102）

- **职责**：建 `service_token` 表、加 `eval_run` 两个 JSON 列、同步 downgrade；变更形态 A。
- **关键依赖**：Alembic + SQLAlchemy core；`sa.JSON().with_variant(postgresql.JSONB, "postgresql")`（`:29`）复刻 `json_type()` 的双库行为——迁移不能 import 应用代码，所以这份 variant 是必要的重复。
- **变更代码块**：

  ```python
  # 新：upgrade()
  op.create_table("service_token", ...,
      sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_service_token_tenant_id_tenant", ondelete="RESTRICT"),
      sa.PrimaryKeyConstraint("id"),
      sa.UniqueConstraint("tenant_id", "name", name="uq_service_token_tenant_name"),
      sa.UniqueConstraint("tenant_id", "id", name="uq_service_token_tenant_id_id"),
      sa.UniqueConstraint("token_hash", name="uq_service_token_token_hash"))
  op.create_index("ix_service_token_tenant_id", ...)
  op.create_index("ix_service_token_tenant_revoked_created", ...)
  op.add_column("eval_run", sa.Column("asset_override_json", _JSON, server_default=sa.text("'{}'"), nullable=False))
  op.add_column("eval_run", sa.Column("evolution_lineage_json", _JSON, server_default=sa.text("'{}'"), nullable=False))
  ```

  为什么这么写：`server_default '{}'` 让**存量 run 不需要回填**（老行为 = 未覆盖），这正是“additive”的数据侧保证；`ondelete=RESTRICT` 与其它租户表一致，租户删除先被拦住。
- **实现思路**：一张表 + 两列 + 四个索引/约束，没有数据迁移脚本——设计上不留历史包袱。
- **检视发现**：迁移把唯一约束显式命名为 `uq_service_token_token_hash`、`uq_service_token_tenant_name` 等；`token_hash` 在模型侧是 `unique=True`，`Base.metadata` 的命名约定（`backend/app/models/base.py:11`–`:17`）恰好生成同名的 `uq_service_token_token_hash`，`ix_service_token_tenant_id` 也与 `tenant_fk()` 的 `index=True` 自动命名一致——两侧语义与名字都对得上，没有漂移。`downgrade` 只删列删表，不回填数据——符合“0035 之前的 run 本来就没有这两个键”的事实。

### 9.4 `backend/app/services/service_tokens.py`（A +433）

- **职责**：机器身份的唯一业务实现：创建/列出/撤销/认证/上限/视图/主体标签；变更形态 A。
- **关键依赖**：`hashlib`（SHA-256，标准库）+ `secrets.token_urlsafe`（CSPRNG 生成明文）；SQLAlchemy `func.count` / `func.coalesce` 做上限统计。没有引入任何新第三方包——这点值得强调：令牌系统最怕“为了 JWT 引入 PyJWT”，这里用最朴素的高熵随机串 + 哈希，攻击面最小。
- **变更代码块**：

  ```python
  # 创建（:217）
  plaintext = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)   # 47 字符
  token = ServiceToken(..., token_hash=_hash_token(plaintext), ...)
  session.add(token); session.commit()                            # IntegrityError → 409
  # 认证（:308）
  token = session.scalar(select(ServiceToken).where(token_hash == _hash_token(raw), tenant_id == DEFAULT))
  if token is None or not _is_active(token): return None
  if previous is None or (current - previous).total_seconds() >= 60: token.last_used_at = current; session.commit()
  # 上限（:343）
  active = count(EvalRun where created_by == actor and status not in TERMINAL)
  spent  = coalesce(sum(EvalRun.budget_spent_cny) where created_by == actor and created_at >= 今天)
  ```

  为什么这么写：①明文只在一处产生、只在一个响应返回，任何持久化路径都拿不到它；②`last_used_at` 60 秒节流是为了“状态轮询不要把读请求变成写请求”；③上限按 `created_by` 而不是另建关联表，是因为 `svc:<name>` 已经是既有审计字段，复用它避免新表与 run 的 FK 迁移。
- **实现思路**（≤3 行）：用高熵 bearer 串做“机器口令”，库内只留哈希；授权信息（scope/白名单/上限）全部挂在令牌行上，鉴权一次查询就能拿到全部判定材料。
- **检视发现**：见 §6.4（best-effort 上限、名称永久占用）。另：`enforce_launch_caps` 的预算窗口起点用 `current.replace(hour=0,...)`，而 `current` 已经是 UTC（`_now`），与文档“UTC 自然日”一致；但 run 的 `created_at` 若是无时区列（SQLite 返回 naive），比较仍能工作（同库同值），无实害。

### 9.5 `backend/app/api/service_tokens.py`（A +93）

- **职责**：三个管理员端点：`POST` 创建（201，明文一次）、`GET` 列表（掩码）、`DELETE` 撤销（204）；变更形态 A。
- **关键依赖**：FastAPI（`APIRouter`/`Depends`/`HTTPException`）+ Pydantic v2（`Field(min_length=1)`、`Literal["eval:launch","eval:read"]`）。Pydantic 的 `Literal` 让 scope 在**HTTP 层**就被拒（422 通用错误体），而 service 层还有一份同义校验兜内部调用——两层都保留是刻意的（`:29` vs `service_tokens.py:140`）。
- **变更代码块**：

  ```python
  @router.post("/api/v1/service-tokens", status_code=201)
  def create_service_token(payload, session, context: AuthContext = Depends(require_admin)):
      token, plaintext = token_service.create_token(session, payload.model_dump(), created_by=context.user.email)
      return {"status": "ok", "service_token": token_service.token_view(token), "token": plaintext}
  ```

  为什么这么写：三个路由全部 `require_admin`（管理员会话），不暴露给服务令牌——**令牌不能自己造令牌**，避免权限自我增殖；`DELETE` 返回 `Response(status_code=204)` 而不是带 body，保持“撤销即空响应”的语义。
- **实现思路**：API 层只做“校验 → 调服务 → 包装错误信封”，无业务判断。
- **检视发现**：无。`_http_error`（`:36`）与模板路由的同名函数逻辑重复（都做 `details.fields` 折叠），但两处分属不同模块、错误基类不同，抽象收益不大，接受。

### 9.6 `backend/app/api/dependencies.py`（M +103）

- **职责**：新增 `Actor` 与 `require_session_or_service(scope)`；变更形态 M（模块 docstring 扩写 + 追加两个函数与两个错误小工厂）。
- **关键依赖**：FastAPI `Depends`/`HTTPException`/`Request`；`identity.resolve_session`（会话解析）与 `token_service.authenticate`（令牌解析）是本文件的两个下游。
- **变更代码块**（旧 vs 新）：

  ```python
  # 旧：只有三条会话依赖
  require_session / optional_session / require_admin

  # 新：+ Actor + 令牌分支
  def dependency(request, session, settings) -> Actor:
      header = request.headers.get("Authorization")
      if header is not None:                      # 有头就只认令牌
          return _service_actor(request, session, header, scope=scope)
      context = _resolve(request, session, settings)
      if context is None: raise _unauthenticated()
      request.state.auth = context                # 会话分支保留既有 state 行为
      if scope == SCOPE_EVAL_LAUNCH and context.role != identity.ROLE_ADMIN:
          raise _forbidden("当前账号没有写入权限。")
      return Actor(label=context.user.email, ...)
  ```

  为什么这么写：①“有头就只走令牌”避免“坏令牌被 Cookie 兜住”；②`request.state.auth` 只在会话分支设置——全仓 grep `state.auth` 只有本文件的 3 处写入、没有读取方，所以服务分支不设它没有副作用；③闭包工厂让路由端只写 `Depends(require_session_or_service("eval:read"))`，scope 与路由一一对应，不靠路由自己记得校验。
- **实现思路**（≤3 行）：把“谁在调用”收敛成一个不可变 `Actor`，两种凭据各自解析后汇入同一主体；授权判定（scope/白名单/角色）就在依赖里一次做完，业务代码不再关心凭据形态。
- **检视发现**：见 §6.2（401/403 口径）、§6.3（未声明 OpenAPI securityScheme）。另有一个小观察：`SCOPE_EVAL_LAUNCH/READ` 常量（`:31`–`:32`）与 `SERVICE_TOKEN_SCOPES` 重复定义，serve 的是“依赖层不直接依赖 service 模块常量”的可读性，可接受。

### 9.7 `backend/app/main.py`（M +11）

- **职责**：挂载 `service_tokens_router`；把 `eval_templates` 与 `results` 从“路由级 `require_session`”改为逐路由声明；变更形态 M。
- **关键依赖**：FastAPI `include_router(dependencies=...)` 的语义——路由级依赖会对该路由**所有**端点生效，因此“某几个端点要放宽/换鉴权”时必须拆掉路由级依赖，逐个声明。
- **变更代码块**：

  ```python
  # 旧
  app.include_router(eval_templates_router, dependencies=[Depends(require_session)])
  app.include_router(results_router, dependencies=[Depends(require_session)])
  # 新
  app.include_router(eval_templates_router)          # 每个路由自己声明
  app.include_router(service_tokens_router)          # 每路由 require_admin
  app.include_router(results_router)                 # status/results 允 eval:read
  ```

  为什么这么写：`results` 里 compare/case-tests 必须保持“仅会话”，status/results 要允许 `eval:read` 令牌；`eval_templates` 里 launch 要允许令牌而 CRUD 保持会话/管理员。路由级依赖无法表达这种“同 router 不同鉴权”，只能拆。
- **实现思路**：把鉴权从“router 级默认值”下沉到“路由显式声明”，并用一条专门的回归测试兜住“拆完没漏”。
- **检视发现**：无——这是本次最容易出错的一处，但results 的四个路由 + 模板的六个路由的显式依赖都能在文件里逐条对上（`backend/app/api/eval_templates.py:164/197/215/235/256/271`、`backend/app/api/results.py:181/261/320/340`），且 `test_eval_templates.py:1503` 覆盖 12 条路径的匿名 401。

### 9.8 `backend/app/api/eval_templates.py`（M +64）

- **职责**：请求模型加 `asset_override`/`lineage`；launch 换 `Actor` 依赖、把覆盖与血缘透传给服务层、按需回显 `asset`/`warnings`；变更形态 M。
- **关键依赖**：Pydantic v2 的 `model_dump(exclude_unset=True)`——这是“字段缺省 vs 显式 null”能区分的关键；`Field(pattern="^[0-9a-f]{40}$")` 在 HTTP 层先挡非法 commit。
- **变更代码块**：

  ```python
  # 新：请求模型
  class AssetOverrideRequest(BaseModel):
      repo_url: str | None = Field(default=None, max_length=500)
      ref: str | None = Field(default=None, max_length=200)
      commit: str | None = Field(default=None, pattern="^[0-9a-f]{40}$")
      asset_paths: list[str] | None = None
      asset_types: dict[str, str] | None = None

  # 新：launch 主体
  asset_override = payload.asset_override.model_dump(exclude_unset=True) if payload.asset_override else None
  run, created, launch_meta = template_service.launch_template(..., created_by=context.label,
      asset_override=asset_override, lineage=lineage, service_token=context.service_token)
  ...
  if launch_meta.get("asset") is not None: body["asset"] = launch_meta["asset"]
  if launch_meta.get("warnings"): body["warnings"] = launch_meta["warnings"]
  ```

  为什么这么写：①`exclude_unset=True` 让“只传了 `commit`”和“显式把 `ref` 置 null”在服务层可区分，从而支持“整字段替换 + 缺省继承”；②`created_by=context.label` 把控制台邮箱与 `svc:<name>` 统一成一种主体标签，幂等键自然按主体隔离；③回显靠 `launch_meta` 而不是重新查一次 run，保证 202 与内部冻结值一致。
- **实现思路**（≤3 行）：API 层只做“模型 → dict → 透传 → 包装响应”的搬运营，不写解析逻辑；回显字段“有才加键”，让控制台响应保持逐字节兼容。
- **检视发现**：`_http_error` 的类型注解扩成 `TemplateError | ServiceTokenError`（`:122`），两者都有 `status/code/details/field`，鸭子类型成立；若将来新增第三种错误基类，记得同步这里（无实害，仅备注）。

### 9.9 `backend/app/services/eval_templates.py`（M +399）

- **职责**：新增覆盖解析/断言/回显/重放；`launch_template` 改成“幂等 → 上限 → 启用 → 上报目标 → 资产解析（分叉）→ prepare/create → 冻结”，返回三元组；变更形态 M。
- **关键依赖**：`asset_service.resolve_assets`（既有资产解析与快照冻结）、`preparation_service.build_preparation/consume_preparation`（既有 prepare/create）、`GitClientError`（Git 访问错误）。本文件没有新第三方依赖，全部是“复用既有件 + 错误映射”。
- **变更代码块**（旧 vs 新，择要）：

  ```python
  # 旧：单一资产来源 + 二元返回
  asset_snapshot_id = _resolve_template_assets(session, template, created_by=..., settings=...)
  ...
  return run, created

  # 新：分叉 + 三元返回
  if asset_override is None:
      asset_snapshot_id = _resolve_template_assets(...)          # 老路径一行未改
  else:
      asset_snapshot_id, launch_asset, effective = _resolve_override_assets(...)
      warnings = _override_warnings(template)
  ...
  run.asset_override_json = {"requested": dict(asset_override), "effective": effective}
  run.evolution_lineage_json = dict(lineage)
  return run, created, {"asset": launch_asset, "warnings": warnings}
  ```

  ```python
  # 新：commit 断言（:1278）
  if commit and snapshot.resolved_commit != commit:
      raise CandidateCommitMismatch(..., details={"requested": commit, "resolved": snapshot.resolved_commit})
  ```

  为什么这么写：①分叉而不是改写老函数，把“控制台路径零变化”变成代码结构上的事实，而不是测试上的承诺；②断言放在 `build_preparation` 之前，保证“不一致就什么都不留”；③`requested`/`effective` 双份冻结，同时服务“审计”和“回显”两个目的，避免读侧重新推导。
- **实现思路**（≤3 行）：把“覆盖”建模成一次**来源重定向**——先换源、再按 commit/ref 解析出快照、断言 commit、然后把快照交给既有的 prepare/create；平台不新增执行路径，只换输入。
- **检视发现**：见 §6.1（多源歧义）、§6.5（空覆盖边界）、§6.6（重放 warnings）。另有两处小的一致性观察：①`_override_source`（`:1188`）在 commit 格式校验（`:1189`）之前执行，所以“坏 commit + 未登记 repo”会先报 `asset_source_unresolved`——错误优先级略反直觉但无实害；②`_override_types`（`:1063`）会静默丢弃非字符串键值，而 `_override_paths` 遇到非法项会 422——同样是“服务层兜底”，两者严格程度不一致（HTTP 层 Pydantic 已挡，实际不可达），可不动。

### 9.10 `backend/app/models/evaluation.py`（M +10）

- **职责**：`eval_run` 增加 `asset_override_json`、`evolution_lineage_json` 两个非空 JSON 列（默认 `{}`）；变更形态 M。
- **关键依赖**：`json_type()` 双库 variant；`TimestampMixin` 提供的 `created_at/updated_at`。
- **变更代码块**：两个 `mapped_column(json_type(), default=dict, nullable=False, server_default="{}")`。为什么这么写：老 run 无需回填；`{}` 是“未覆盖/无血缘”的零值，读侧 `(x or {})` 判空即可。
- **实现思路**：把“覆盖请求 + 归一化身份 + 血缘”冻结进 run 本身，而不是靠关联表——一次查询即可回显，且天然随 run 生命周期不可变。
- **检视发现**：无。注意 `asset_override_json` 同时存**原始请求**与 `effective`，字段名说的是 override，实际语义是“覆盖审计包”；命名略窄但注释已说明（`:159`–`:162`）。

### 9.11 `backend/app/api/results.py`（M +27）

- **职责**：status/results 换成 `require_session_or_service("eval:read")`；两个读模型增量回显 `asset`（results 另回显 `lineage`）；compare/case-tests 显式保留 `require_session`；变更形态 M。
- **关键依赖**：`run_asset_identity`（服务层）、`Actor`（依赖层）；`_run_or_404` 保证租户内查找。
- **变更代码块**：

  ```python
  # 旧
  def get_eval_results(run_id: str, session: Session = Depends(get_session)): ...
  # 新
  def get_eval_results(run_id, session=Depends(get_session),
                       _actor: Actor = Depends(require_session_or_service("eval:read"))): ...
      ...
      asset = run_asset_identity(session, run)
      if asset is not None: response["asset"] = asset
      lineage = run.evolution_lineage_json or {}
      if lineage: response["lineage"] = lineage
  ```

  为什么这么写：①`_actor` 前导下划线表明“这个端点只需要鉴权、不需要主体信息”，可读性上区分于 launch；②“有才加键”保证旧 run 的响应形状不变（严格增量）；③compare/case-tests 保留 `require_session` 是刻意的——它们返回完整用例内容，面比 status/results 大，不在 #429 的开放范围内。
- **实现思路**（≤3 行）：读模型的增量字段全部走同一个 `run_asset_identity`，status 与 results 不会出现两种回显口径。
- **检视发现**：无功能发现。每次轮询多一次 `session.get(AgentAssetSnapshot)`（主键查，代价可忽略）；若将来 status 的 QPS 很高，可考虑把 identity 冗余进 run 行，但目前不需要。

### 9.12 `docs/design/eval-platform-architecture.md`（M +6/−5）

- **职责**：§5.14 三处改写（覆盖语义、鉴权句、错误码/回显句）+ 末段补上报提示 + 表 2 两行（eval_run 追加字段、新增 service_token 行）；变更形态 M。
- **关键依赖**：仓库文档门禁（`lint:docs`/`verify:docs` 查双语镜像、围栏、预算）；`scripts/doc-budgets.manifest.json` 登记词数上限。
- **变更代码块**：以第 185 行为例——旧句“模板启动不接受配置覆盖……”改为“控制台一键启动不接受配置覆盖……；机器触发（服务令牌）可携带可选 `asset_override`……commit 为权威身份且必须断言……`ref` 仅作展示审计”。为什么这么写：把“谁能覆盖”说清楚，避免读成“模板彻底不能覆盖”；把 commit 断言与 422 拒绝写成硬约束，和实现一一对应。
- **实现思路**：只加“现状事实”，不复述设计理由（理由留在 issue/本检视文档），符合 `docs/AGENTS.md` 的“文档写现状”。
- **检视发现**：见 §6.3（429/401 未写）、§6.7（回显范围未写一句）。另：第 207 行的错误码句沿用“403 表示当前账号没有启动权限”，未区分“令牌 scope/白名单不足也是 403”，读者可能把 403 只当角色问题——可在 429/401 补句时一并润色。

### 9.13 `docs/design/eval-platform-architecture.en.md`（M +6/−5）

- **职责**：英文侧逐句镜像；变更形态 M。
- **关键依赖**：无；受 `.i18n.yaml` blob 配对与 `verify:docs` 的“围栏/表格/列表镜像”约束。
- **变更代码块**：`Console one-click launches accept no configuration overrides...`（第 185 行）、`All three reuse "console session or service token" authentication...`（第 197 行）、`An override launch echoes asset...`（第 207 行）、`Machine triggers with asset_override inherit...`（第 211 行）、表 2 的 `service_token` 行（第 233 行）。
- **实现思路**：与中文同构，长句一行到底（仓库文档风格）。
- **检视发现**：本次按 diff 逐句对照中文，未发现语义漂移；`verify:docs` 的 CI 绿也说明镜像结构成立。

### 9.14 `docs/design/eval-platform-architecture.i18n.yaml`（M +2/−2）

- **职责**：重录中英两侧 blob hash；变更形态 M。
- **关键依赖**：`npm run record:i18n -- <base.md>` 生成。
- **变更代码块**：两个 40 位 hash 更新（`b5b6d4e…` / `d9310e6…`）。为什么必须改：配对门禁以 blob 为准，漏录会让 `verify:docs` 判“两侧不一致”。
- **实现思路**：纯记录。
- **检视发现**：无；CI `docs` 绿说明重录生效。

### 9.15 `scripts/doc-budgets.manifest.json`（M +2/−2）

- **职责**：架构文档预算 13500 → 13800，并在 `//` 说明里登记理由（M10 #429 的机器触发契约 + 表 2 新行）；变更形态 M。
- **关键依赖**：仓库文档预算门禁读取该文件。
- **变更代码块**：`"docs/design/eval-platform-architecture.md": 13800` + `//` 串尾追加一段带编号与增量的说明。为什么这么写：仓库规则要求“提高上限须在 PR 中说明”，而说明只写增量事实（13500 → 13800），不重复文档内容。
- **实现思路**：预算随事实增长，理由串本身就是账本。
- **检视发现**：无。若 §6.3 决定再补 429/401 两句，需留意是否顶到 13800——届时优先压缩旧句而不是继续提预算。

### 9.16 `.agents/flow/issues/m10-candidate-launch-override.md`（M +5/−1）

- **职责**：`status: ready → in-progress`；`scope` 补 4 个实际改动路径（`models/__init__.py`、两个 revision 断言测试、`doc-budgets.manifest.json`）；变更形态 M。
- **关键依赖**：`verify-flow` 的机读围栏（`yaml flow`）与 scope 校验——PR 改动路径必须落在 scope 内，否则 `verify-flow --diff origin/main` 红。
- **变更代码块**：scope 列表追加四行 + status 一行。为什么这么写：scope 是“允许改动的白名单”，实现过程中发现需要动 `doc-budgets` 与版本断言，就必须把白名单同步扩进去，而不是绕过门禁。
- **实现思路**：流程文件与代码同 PR 演进，门禁才是可信的。
- **检视发现**：scope 扩容属于“事后补白名单”，严格说应在动那些文件之前先改 scope；但本 PR 是同一个提交集内自洽（`1e7ae4b` 专门做这件事），且 CI flow 门禁绿，可接受。另：scope 里 `tests/backend/test_results.py` 最终没改，属“允许但未用”，无影响。

### 9.17 `.agents/flow/issues/m10-candidate-launch-override.en.md`（M +5/−1）

- **职责**：英文镜像（同 status/scope）；变更形态 M。
- **关键依赖**：中英围栏必须逐字节一致（README §机读围栏），scope 列表因此两侧同步。
- **变更代码块**：与中文同构的 5 行。
- **实现思路**：镜像维护。
- **检视发现**：diff 对照无漂移。

### 9.18 `.agents/flow/issues/m10-candidate-launch-override.i18n.yaml`（M +2/−2）

- **职责**：重录 flow 三件套的配对 hash；变更形态 M。
- **关键依赖**：`record:i18n`。
- **变更代码块**：两个 hash 更新。为什么：改了 md 必须重录，否则 `verify:flow` 判配对失败。
- **实现思路**：纯记录。
- **检视发现**：无。

### 9.19 `tests/backend/test_service_tokens.py`（A +590）

- **职责**：令牌域单测 + 依赖域单测 + 上限单测；变更形态 A。
- **关键依赖**：`fastapi.testclient.TestClient`（真跑 ASGI，不 mock 路由）、`starlette.requests.Request`（手工构造请求直接调依赖，省掉为每个 scope 组合造路由）、SQLite 会话 fixture。`_request()`（`:80`）用最小 ASGI scope 手工拼请求是这里的巧劲：依赖是纯函数，不必为它铺 HTTP。
- **变更代码块**（测试即规格，择要）：

  ```python
  # 哈希与一次性明文（:128）
  assert response.text.count(plaintext) == 1
  assert stored.token_hash == hashlib.sha256(plaintext.encode()).hexdigest()
  # 依赖的“坏头不回退 Cookie”（:438）
  with pytest.raises(HTTPException): _call_dependency(..., _request(authorization="Basic abc", cookie=cookie))
  assert raised.value.status_code == 401
  # 上限（:527、:555）
  run.status = "completed"; session.commit(); assert enforce_launch_caps(...) is None
  ```

  为什么这么写：把“安全契约”直接写成断言（明文只出现一次、库里只有哈希、坏头必 401、过期/撤销即失效、上限只算自己主体），比文档承诺更可靠。
- **实现思路**（≤3 行）：分四组（创建/撤销/依赖/上限），每组用最小 fixture 构造，避免共享状态；对“不该发生”的分支（如匿名管理、坏 scope）逐个反证。
- **检视发现**：无遗漏感。唯一未覆盖的是“并发创建同名令牌时 `IntegrityError` → 409”的真实竞态（`:262` 的分支没有被并发测试触发），属可接受的测试成本。

### 9.20 `tests/backend/test_eval_templates.py`（M +871）

- **职责**：新增候选覆盖端到端用例（fake GitHub + StubObjectStore 真跑 `resolve_assets`）、鉴权回归、令牌上限、读回显；变更形态 M（追加在既有文件尾部）。
- **关键依赖**：`monkeypatch.setattr(agent_assets, "GitHubClient", ...)`（`FakeGitHubClient`，`:1146`）把网络替换成内存仓库；`StubObjectStore` fixture（`:1212`）提供 S3 形状的本地对象存储，让快照/清单真的写一遍。`FakeRepo.resolved_refs`（`:1136`）记录“provider 被问了哪个 ref”，用来证明“commit 权威、ref 从未参与解析”。
- **变更代码块**：

  ```python
  # 旧：只有控制台启动用例
  # 新：候选覆盖主用例（:1328）——202 回显 + run 冻结 + status/results + 重放
  assert replay.json()["asset"] == asset          # 重放回显同一身份
  # 失败矩阵（:1540/:1573/:1598/:1626/:1661）——每类错误都断言 _run_count == 0
  # 鉴权回归（:1503）——12 条路径匿名必须 401
  for method, path in protected: assert client.request(method, path, json={}).status_code == 401
  ```

  为什么这么写：①用 fake GitHub 而不是打真网，测试可在 CI 离线跑且能注入“解析漂移/凭据失效”；②每个失败用例都反证“没有建 run”，把“失败不留半成品”变成可执行断言；③golden 注入用例（`:1686`）同时校验 manifest sha256 与“同一 commit 的 tree/blob”，证明“注入的内容确实来自该 commit”。
- **实现思路**（≤3 行）：以“202 → run 行 → status → results → 重放”为主轴写端到端；失败路径按错误码分文件块，每条都带 details 断言，便于将来定位契约变更。
- **检视发现**：`test_launch_asset_override_pins_candidate_commit_and_echoes`（`:1328`）里预置了 `warnings` 不在响应中的断言，配合 `:1728` 的上报模板用例，把“只在覆盖 + 开上报时提示”钉住了。未覆盖：`asset_override.repo_url` 命中多个源时的选择（§6.1 的分支），以及 429 与 `enabled=false` 同时成立时的错误优先级（§6.4）——都属边界，可接受。

### 9.21 `tests/backend/test_operations.py`（M +1/−1）

- **职责**：`database["revision"]` 断言从 `0034_pool_unique_labels` 改为 `0035_service_token`；变更形态 M。
- **关键依赖**：无。
- **变更代码块**：一行字符串。
- **实现思路**：迁移 head 变了就同步运维读模型的断言，避免“部署检查拿旧 head 当预期”。
- **检视发现**：无。这类“head 版本硬编码”会随每次迁移改动一次，属仓库既有约定。

### 9.22 `tests/backend/test_deployment_migration.py`（M +1/−1）

- **职责**：`alembic_version` 断言同步到 `0035_service_token`；变更形态 M。
- **关键依赖**：`database_url` fixture 真跑迁移链。
- **变更代码块**：一行字符串。
- **实现思路**：与 9.21 同源——迁移 head 是部署契约的一部分。
- **检视发现**：无；与 0035 的 `down_revision`（`:25`）一致，CI `deployment contract` 绿。

---

## 检视小结（给拍板用）

- **功能正确性**：主线（令牌鉴权 + 候选注入 + commit 断言 + 回显 + 幂等）在代码、单测、真实冒烟三层自洽；控制台路径零改动的承诺在结构上成立（老函数未改、响应有值才加键）。
- **最值得先讨论的三点**：①`repo_url` 多源歧义（§6.1，功能性、可低成本收紧）；②401/429 的调用方契约缺口（§6.2+§6.3，直接影响 HE 适配器）；③令牌名永久占用与上限 best-effort 的运维语义（§6.4，影响长期运维）。
- **没有发现必须阻断合并的实现缺陷**；上述均为文档/边界/后续项，建议在合并前至少把 §6.3 的文档补句与 PR 描述回填列入同一批。

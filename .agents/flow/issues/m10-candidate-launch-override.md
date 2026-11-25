# [m10] 评测模板机器触发：可选参数注入候选 commit 与服务令牌

[English](m10-candidate-launch-override.en.md) | 中文

```yaml flow
kind: issue
milestone: m10
priority: P0
status: ready
scope:
  - .agents/flow/issues/m10-candidate-launch-override.md
  - .agents/flow/issues/m10-candidate-launch-override.en.md
  - .agents/flow/issues/m10-candidate-launch-override.i18n.yaml
  - plans/2026-11-25-m10-candidate-launch-override/**
  - backend/app/api/eval_templates.py
  - backend/app/services/eval_templates.py
  - backend/app/api/results.py
  - backend/app/api/dependencies.py
  - backend/app/models/evaluation.py
  - backend/app/models/identity.py
  - backend/app/services/service_tokens.py
  - backend/app/api/service_tokens.py
  - backend/app/main.py
  - backend/migrations/**
  - tests/backend/test_eval_templates.py
  - tests/backend/test_results.py
  - tests/backend/test_service_tokens.py
  - docs/design/eval-platform-architecture.md
  - docs/design/eval-platform-architecture.en.md
  - docs/design/eval-platform-architecture.i18n.yaml
adr: []
github:
  number: 429
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/429
```

## 背景

M10 的三个稳定执行接口是控制台会话语义：launch 仅接受管理员 Cookie 会话、body 只有幂等键与可选名称，状态与结果也在会话之后；而 harness-evolution 的自进化迭代需要以机器身份触发评测——每轮发布候选 commit 后，把一次模板评测钉在该 commit 上启动、轮询状态并回取测量。2026-11-25 与需求方讨论后收敛为本 issue，完整分析过程与讨论记录见文末「交付物」。

## 分析过程

- 覆盖管道已存在：run 级 `POST /api/v1/evaluations` 早已支持 `asset_source_id / requested_ref / asset_paths / asset_types` 并冻结不可变资产快照（`backend/app/api/evaluations.py`、`backend/app/services/agent_assets.py`），缺的只是模板启动上的参数入口。
- 资产快照按 commit 冻结（`resolved_commit`、tree digest、bundle），启动后分支移动不影响运行；`resolve_commit` 接受 branch/tag/SHA 且 SHA 解析返回自身，天然支持「commit 权威 + 断言」。
- 模板启动链路（`launch_template`）目前只按模板自己的 source+ref 解析资产；加入覆盖参数即可复用既有 `_resolve_template_assets → build_preparation → consume_preparation`，不需要新执行路径。
- 平台没有机器身份：launch 需要管理员会话、状态与结果在会话之后；HE 无法持浏览器会话，服务令牌是唯一必须新增的身份层。
- 平台状态机有 11 态（含 partial/paused/cancelled），HE 轮询器只认 5 态；按 D1 由 HE 适配，平台契约不为此改变。
- 平台结果只有测量（指标项 + `is_final`），没有 verdict；按 D4 裁决在 HE，候选评测模板不开启自进化上报（D5），重复上报问题不成立。

## 与需求方的讨论结论

以下五条决策由需求方在 2026-11-25 逐条确认；V1 差距分析据此收敛为 V2 方案，本 issue 即其落地载体。

| 决策 | 结论 | 对设计的影响 |
|---|---|---|
| D1 契约方向 | HE 适配平台机制，平台契约不向通用 `/evals` 形状靠拢 | 新逻辑按平台现有风格设计，HE 写适配器 |
| D2 注入参数 | 触发接口支持可选参数：代码仓 + commit + 资产配置 | launch 增加受控覆盖块，commit 为权威身份并断言解析一致 |
| D3 跨运行生效 | 平台不承担晋升；评测完由 HE 提 PR，人工裁决合入 | 平台无晋升/合并设计，模板 Ref 指向由运维约定 |
| D4 测量与裁决 | 平台只给测量，裁决在 HE | 平台不加 verdict/策略字段，结果接口保持测量语义 |
| D5 bad case 上报 | 自进化评测模板不开启上报 | 重复上报/去重不成立，覆盖启动与上报配置不一致时仅提示 |

## 需求

1. HE 负责适配平台机制；平台契约按自身风格演进，不为 HE 改成通用 `/evals` 形状。
2. 评测触发接口必须支持可选参数：能把某个代码仓、某个 commit id 的资产配置注入本次评测；不传参数时保持模板一键启动语义不变。
3. commit 是权威身份：注入必须按 commit 解析、冻结快照并断言解析结果一致；ref 只作展示审计。
4. 平台只返回测量，不做 accept/reject 裁决；裁决策略由 HE 实现。
5. 候选评测使用的评测模板不开启自进化 bad case 上报；平台不为此设计去重。
6. 跨运行生效不在平台范围内：评测完成后由 HE 提交 PR，由人判断是否合入。

## 方案概述

平台侧改动集中在三个文件加一张新表：请求模型加可选字段、launch 支持覆盖解析与断言、status/results 回显资产身份，以及服务令牌表；资产解析、快照冻结与 prepare/create 链路全部复用既有实现，不新增执行路径。

交互流程：HE 发布候选 → 携带 `asset_override` 调 launch → 平台校验并冻结快照、回显 `resolved_commit` → HE 比对后轮询 status → 终态取 results 并换算为裁决输入（目标分/覆盖率/护栏）→ HE 裁决并推进迭代；晋升由 HE 提 PR 人工合入（D3，不在平台范围）。

- `LaunchTemplateRequest` 增加可选 `asset_override`（repo_url/ref/commit/asset_paths/asset_types）与 `lineage`，字段整体替换、缺省继承模板。
- `launch_template` 支持覆盖解析：repo_url 缺省用模板资产源，否则按 repo_url 匹配已登记资产源；有 commit 按其解析并断言 `resolved_commit` 一致；run 写入 `asset_override_json / evolution_lineage_json`。
- status/results 回显 `asset` 块（resolved_commit、asset_snapshot_id、requested_ref），results 另回显 lineage（提供时）。
- 新增 `service_token` 表与 `POST/GET/DELETE /api/v1/service-tokens`；launch 接受「管理员会话或服务令牌」，status/results 接受「任意会话或 `eval:read` 令牌」，支持模板白名单与并发/预算上限。

HE 侧适配（网关适配器、状态映射、结果映射、基线与裁决）在 harness-evolution 仓库另行推进，不属于本 issue。

## 接口变更

### 变更前

```text
POST /api/v1/eval-templates/{template_id}/launch
  auth:    console session (admin role only); no machine identity
  headers: Idempotency-Key (optional)
  body:    { "idempotency_key": string | null, "name": string | null }
  202 ->   { "run_id", "template_id", "status", "status_url", "result_url", "created" }

GET /api/v1/eval-runs/{run_id}/status
  -> { "run_id", "template_id", "template_version", "status", "terminal", "total_cases",
       "queued_count", "running_count", "succeeded_count", "failed_count", "finished_count",
       "progress": { "total", "finished", "percent" }, "resolved_count", "scored_count",
       "resolve_rate", "started_at", "finished_at", "duration_seconds",
       "status_url", "result_url" }

GET /api/v1/eval-runs/{run_id}/results
  -> { "run_id", "template_id", "status", "is_final", "status_url", "result_url",
       "items": [ { "metric_key", "value", "is_final", ... } ], "token_summary" }
```

### 变更后

```text
POST /api/v1/eval-templates/{template_id}/launch
  auth:    admin session OR service token (Bearer hev_..., scope eval:launch,
           optional template allowlist and budget caps)
  headers: Idempotency-Key (unchanged; replay returns the same run)
  body:    { "idempotency_key": string | null, "name": string | null,
             "asset_override": {                    # all optional; absent = inherit template
               "repo_url": string,                  # default: template asset source
               "ref": string,                       # display/audit only
               "commit": string,                    # 40-hex; authoritative identity
               "asset_paths": [string],             # whole-field replacement
               "asset_types": { string: string } }, # whole-field replacement
             "lineage": {                           # optional; stored on the run only
               "evolutionRunId": string, "evolutionRoundId": string,
               "roundSeq": int, "parentCommit": string } }
  202 ->   previous fields + "asset": { "repo_url", "requested_ref", "requested_commit",
             "resolved_commit", "asset_snapshot_id", "bundle_digest" }
             + "warnings": [ ... ]                  # only when override is present and
                                                    # the template enables self-evolution
                                                    # reporting
  422 ->   candidate_commit_mismatch | asset_source_unresolved | asset_ref_unresolved |
           asset_selection_empty | asset_path_not_found (no run is created)

GET /api/v1/eval-runs/{run_id}/status
  -> previous fields + "asset": { "resolved_commit", "asset_snapshot_id", "requested_ref" }

GET /api/v1/eval-runs/{run_id}/results
  -> previous fields + "asset" (same shape) + "lineage" (echoed when provided)
```

### 输入与返回变化

| 接口 | 输入变化 | 返回变化 |
|---|---|---|
| launch | 新增可选 `asset_override` 与 `lineage`；鉴权从「仅管理员会话」扩展为「管理员会话或服务令牌」 | 新增 `asset` 块与可能的 `warnings`；其余字段与幂等语义不变 |
| status | 无输入变化（机器可凭 `eval:read` 令牌读取） | 新增 `asset` 块 |
| results | 无输入变化 | 新增 `asset` 块与 `lineage`（提供时回显） |
| service-tokens（新增） | `POST /api/v1/service-tokens` 创建（明文仅在创建响应返回一次）、`GET` 列表（掩码）、`DELETE` 撤销 | 令牌只存哈希，任何响应、日志与快照不回显明文 |

### 新增错误码

| 错误码（HTTP） | 触发条件 |
|---|---|
| `candidate_commit_mismatch`（422） | 解析到的 commit 与请求 commit 不一致；绝不静默评测 |
| `asset_source_unresolved`（422） | `asset_override.repo_url` 无法按 repo_url 匹配到已登记资产源（凭据不可确定） |
| `asset_ref_unresolved`（422） | commit/ref 在资产源仓库不可解析（仓库不可达、凭据失效或对象不存在） |
| `asset_selection_empty`（422，既有码复用） | 覆盖或继承后的资产选择为空 |
| `asset_path_not_found`（422，既有码复用） | 资产路径在该 commit 不存在 |

## 交付物

- [OpenAPI 变更稿](../../../plans/2026-11-25-m10-candidate-launch-override/openapi-change.md)：schema 级 diff（请求模型、响应扩展、错误码、服务令牌端点）。
- [文档 §5.14 修订文案](../../../plans/2026-11-25-m10-candidate-launch-override/docs-5-14-revision.md)：中英对照的替换段落，实施时按平台文档流程落库。

## 验收

- 带覆盖 launch 一个真实候选 commit：202 响应的 `asset.resolved_commit` 逐字等于请求 commit，status/results 回显一致。
- golden 注入证明：候选改一个 SKILL.md，快照 manifest 中该文件 digest 与该 commit 中对应 blob 的 digest 一致。
- commit 权威：同时给 ref 与 commit 时按 commit 解析、ref 原样回显；仅给不可解析的 commit 时 422 `asset_ref_unresolved` 且不创建 run。
- 跨仓注入：`repo_url` 未登记时 422 `asset_source_unresolved`；登记后同一请求成功。
- 覆盖后选择为空或路径不存在：422 `asset_selection_empty / asset_path_not_found`，不创建 run。
- 不带 `asset_override` 的控制台一键启动行为不变：响应无 `asset` 块，权限、幂等与校验语义不变。
- 服务令牌：无令牌/错 scope/模板不在白名单 → 403；审计记录 `created_by=svc:<name>`；幂等重放返回同一 run；`npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与 PR CI 通过。

## 非目标

- 不改评测执行、调度、评分与轨迹链路；覆盖只影响资产来源解析。
- 不做 accept/reject 裁决与任何策略字段（D4）；裁决由 HE 实现。
- 不做晋升、合并与回滚（D3）；模板 Ref 指向合并后分支或发布 tag 由运维约定。
- 不做 bad case 去重或上报模式设计（D5）；上报语义完全由模板配置决定。
- HE 侧适配（网关、映射与裁决）不在本仓库实现。

## 参考

- 设计：[docs/design/eval-platform-architecture.md](../../../docs/design/eval-platform-architecture.md) §5.14（评测模板与一键启动）。
- 相关：issue #378（评测模板）、#382（模板级自进化上报）、#410（模板来源展示）。
- 平台状态机与结果读模型：`backend/app/models/evaluation.py`、`backend/app/api/results.py`。
- 既有覆盖管道证据：`backend/app/api/evaluations.py`（CreateEvaluationRequest）、`backend/app/services/agent_assets.py`（resolve_assets）。

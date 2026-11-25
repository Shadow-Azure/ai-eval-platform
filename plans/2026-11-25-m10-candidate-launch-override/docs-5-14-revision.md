# 文档 §5.14 修订文案 — M11 评测模板机器触发

- 目标文件：`docs/design/eval-platform-architecture.md`（中文 base）与 `docs/design/eval-platform-architecture.en.md`（英文侧）。
- 本文件只给「替换文案」，不在本 PR 中直接改常驻文档；实施 issue 落地时按下列 before/after 逐句替换，改完任一侧后最小修补另一侧并运行：

```sh
npm run record:i18n -- docs/design/eval-platform-architecture.md
npm run lint:docs
npm run verify:docs
```

- 注意：该文档预算当前为 13500 词；实施时若超出，按门禁提示压缩或在本 manifest 登记 `//` 说明后提预算。
- 结构镜像约束：两侧段落与表格行列保持镜像；新增句子的中英文各自成段（一行到底）。

## 修订 1：覆盖参数（替换 §5.14 第二段中的一句）

### 中文侧

Before（现网原文，段内最后一句）：

> 模板启动不接受配置覆盖，保证“一键”语义与结果可复现；需要不同组合时复制模板再编辑，或走现有的自定义发起流程。

After（替换为）：

> 控制台一键启动不接受配置覆盖，保证“一键”语义与结果可复现，需要不同组合时复制模板再编辑或走自定义发起流程；机器触发（服务令牌）可在启动请求携带可选 `asset_override`（repo_url、ref、commit、asset_paths、asset_types），把一次评测钉在指定代码仓的指定候选 commit 上，字段整体替换、缺省继承模板，commit 为权威身份且平台必须断言解析出的 `resolved_commit` 与请求 commit 一致，不一致即 422 拒绝、绝不静默评测，`ref` 仅作展示审计。

### 英文侧

Before（现网原文，段内最后一句）：

> A template launch accepts no configuration overrides, preserving the one-click semantics and reproducibility; use a copied and edited template, or the existing custom launch flow, for a different combination.

After（替换为）：

> Console one-click launches accept no configuration overrides, preserving the one-click semantics and reproducibility, so a different combination uses a copied and edited template or the existing custom launch flow; machine triggers (service tokens) may carry an optional `asset_override` (repo_url, ref, commit, asset_paths, asset_types) to pin one evaluation to a chosen repository and candidate commit, fields are replaced as a whole with absent fields inheriting the template, the commit is the authoritative identity, and the platform must assert that the resolved commit equals the requested commit, rejecting with 422 on a mismatch instead of ever evaluating silently, while ref is display and audit only.

## 修订 2：机器身份（替换 §5.14「三个稳定接口」段的鉴权句）

### 中文侧

Before（现网原文）：

> 三个接口都复用控制台会话鉴权：启动需要管理员权限，状态与结果对已登录用户只读；首期不提供独立的机器令牌。

After（替换为）：

> 三个接口复用「控制台会话或服务令牌」鉴权：启动需要管理员权限或持 `eval:launch` 的服务令牌，状态与结果对已登录用户或持 `eval:read` 的服务令牌只读；服务令牌由管理员在 `/api/v1/service-tokens` 创建、列表与撤销，支持 scope、模板白名单与并发/预算上限，明文仅在创建响应返回一次，库内只存哈希，机器启动的 `created_by` 落 `svc:<名称>` 并进入既有审计与幂等约束。

### 英文侧

Before（现网原文）：

> All three reuse console session authentication: launching requires administrator permission, while status and results are read-only for signed-in users; the first release does not provide a separate machine token.

After（替换为）：

> All three reuse "console session or service token" authentication: launching requires administrator permission or a service token with `eval:launch`, while status and results are read-only for signed-in users or a service token with `eval:read`; administrators create, list, and revoke service tokens at `/api/v1/service-tokens`, with scopes, a template allowlist, and concurrency/budget caps, the plaintext is returned exactly once in the creation response and only its hash is stored, and machine launches record `created_by` as `svc:<name>` in the existing audit and idempotency constraints.

## 修订 3：启动语义段补回显与错误码（§5.14「启动接口先校验…」段句尾追加）

### 中文侧

Before（现网原文，§5.14 表 12 后一段）：

> 启动接口先校验模板存在且启用、引用对象仍可用，再走既有的 prepare/create 流程；返回 404 表示模板或评测不存在，409 表示模板停用或幂等键冲突，422 表示引用对象失效或字段不可用，403 表示当前账号没有启动权限。

After（段尾追加一句）：

> 启动接口先校验模板存在且启用、引用对象仍可用，再走既有的 prepare/create 流程；返回 404 表示模板或评测不存在，409 表示模板停用或幂等键冲突，422 表示引用对象失效或字段不可用，403 表示当前账号没有启动权限。带覆盖的启动在 202 响应回显 `asset`（resolved_commit、asset_snapshot_id、bundle_digest），status 与 results 同样回显该身份供调用方比对，新增 422 错误码 `candidate_commit_mismatch`、`asset_source_unresolved`、`asset_ref_unresolved`（`asset_selection_empty` 与 `asset_path_not_found` 复用既有码），任一命中即不创建评测。

### 英文侧

Before（现网原文）：

> The launch interface first validates that the template exists and is enabled and that its references are usable, then walks the existing prepare/create flow; 404 means the template or evaluation does not exist, 409 means the template is disabled or an idempotency-key conflict, 422 means a reference is invalid or a field is unusable, and 403 means the current account lacks launch permission.

After（段尾追加一句）：

> The launch interface first validates that the template exists and is enabled and that its references are usable, then walks the existing prepare/create flow; 404 means the template or evaluation does not exist, 409 means the template is disabled or an idempotency-key conflict, 422 means a reference is invalid or a field is unusable, and 403 means the current account lacks launch permission. An override launch echoes `asset` (resolved_commit, asset_snapshot_id, bundle_digest) in the 202 response, status and results echo the same identity for caller-side comparison, and new 422 codes `candidate_commit_mismatch`, `asset_source_unresolved`, and `asset_ref_unresolved` join the reused `asset_selection_empty` and `asset_path_not_found`, none of which create an evaluation.

## 修订 4：上报与覆盖并存的提示（§5.14 末段句尾追加，可选）

### 中文侧

Before（现网原文，§5.14 最后一段）：

> 启动在创建运行前解密保存的令牌，把它与其他上报字段一并传入既有 prepare/create 链路，从模板启动的评测在终态按 M9 #266/#341 outbox 上报 bad case；启用但配置不完整或密文无法解密时，启动在创建运行前返回 422。

After（段尾追加一句）：

> 启动在创建运行前解密保存的令牌，把它与其他上报字段一并传入既有 prepare/create 链路，从模板启动的评测在终态按 M9 #266/#341 outbox 上报 bad case；启用但配置不完整或密文无法解密时，启动在创建运行前返回 422。带 `asset_override` 的机器触发沿用模板的上报语义，不自增开关也不做去重；自进化候选评测应使用未启用上报的模板，模板启用上报时带覆盖启动在响应 `warnings` 中提示一次。

### 英文侧

Before（现网原文，对位段）：

> The launch decrypts the stored token before creating the run and passes it with the other reporting fields into the existing prepare/create chain, so template-launched evaluations report bad cases through the M9 #266/#341 outbox at a terminal state; when enabled but incompletely configured or undecryptable, the launch answers 422 before creating a run.

After（段尾追加一句）：

> The launch decrypts the stored token before creating the run and passes it with the other reporting fields into the existing prepare/create chain, so template-launched evaluations report bad cases through the M9 #266/#341 outbox at a terminal state; when enabled but incompletely configured or undecryptable, the launch answers 422 before creating a run. Machine triggers with `asset_override` inherit the template's reporting semantics with no extra switch and no dedup; self-evolution candidate evaluations should use a template with reporting disabled, and an override launch against a reporting-enabled template surfaces one warning in the response `warnings`.

## 修订 5：数据模型表（§6 表 2，可选但建议）

### 中文侧

表 2 中 `eval_run / eval_case_run` 行的「关键字段」列，句尾追加：`、资产覆盖与血缘（asset_override、evolution lineage）`；如新增服务令牌行，则在表尾追加一行：`| service_token | 机器触发身份 | 哈希令牌、scope、模板白名单、并发/预算上限、最近使用与撤销状态 |`。

### 英文侧

镜像修改：追加 `; asset override and lineage (asset_override, evolution lineage)`；表尾新增行同义翻译：`| service_token | Machine-trigger identity | hashed token, scopes, template allowlist, concurrency/budget caps, last-used and revocation state |`。

## 实施清单（落到常驻文档时的顺序）

1. 按「修订 1/2/3/4/5」逐句替换中英两侧；保持段落一行到底与表格镜像。
2. `npm run record:i18n -- docs/design/eval-platform-architecture.md` 重录配对。
3. `npm run lint:docs` → `npm run verify:docs`；预算超限时按门禁提示压缩或登记提预算说明。
4. 同步检查 §5.14 接口示例块（若实施时补 curl 示例，两侧围栏逐字一致）。

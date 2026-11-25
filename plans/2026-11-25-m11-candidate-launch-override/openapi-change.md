# OpenAPI 变更稿 — M11 评测模板机器触发（候选 commit 注入 + 服务令牌）

- 基线：main @ `a038388`（M10 评测模板三稳定接口）。
- 性质：**纯增量（additive）**。既有字段、既有错误码、既有幂等语义全部不变；不带 `asset_override` 的请求（控制台一键启动）行为与响应形状完全不变。
- 平台 OpenAPI 由 FastAPI 运行时生成，本文给出实现用的 schema 级 diff；实施后以 `docs/design/eval-platform-architecture.md` §5.14 修订文案（同目录 `docs-5-14-revision.md`）同步常驻文档。

## 0. 变更总览

| 位置 | 变更 | 类型 |
|---|---|---|
| `components.securitySchemes` | 新增 `serviceToken`（Bearer，前缀 `hev_`） | 新增 |
| `POST /api/v1/eval-templates/{template_id}/launch` | requestBody 新增可选 `asset_override`、`lineage`；202 响应新增 `asset`、`warnings`；新增 422 错误码 | 扩展 |
| `GET /api/v1/eval-runs/{run_id}/status` | 响应新增 `asset` | 扩展 |
| `GET /api/v1/eval-runs/{run_id}/results` | 响应新增 `asset`、`lineage` | 扩展 |
| `POST/GET/DELETE /api/v1/service-tokens` | 服务令牌管理端点（管理员） | 新增 |
| 错误码 | 新增 `candidate_commit_mismatch`、`asset_source_unresolved`、`asset_ref_unresolved`；复用既有 `asset_selection_empty`、`asset_path_not_found` | 新增/复用 |

## 1. securitySchemes

```yaml
components:
  securitySchemes:
    serviceToken:
      type: http
      scheme: bearer
      bearerFormat: hev_
      description: >
        Machine identity for harness-evolution (or other services). Scopes:
        eval:launch (template launch), eval:read (status/results reads).
        Optional per-token template allowlist and concurrency/budget caps.
        Stored hashed; the plaintext is returned exactly once at creation.
```

launch 的 security 组合由实现改为「`sessionCookie` 或 `serviceToken`（需 `eval:launch`）」；status/results 为「`sessionCookie` 或 `serviceToken`（需 `eval:read`）」。FastAPI 侧对应新的 `require_session_or_service` 依赖，OpenAPI 中以 `security: [{ sessionCookie: [] }, { serviceToken: [] }]` 表达。

## 2. `POST /api/v1/eval-templates/{template_id}/launch`

### 2.1 requestBody（扩展前 → 扩展后）

扩展前（现网行为，保留）：

```yaml
requestBody:
  required: true
  content:
    application/json:
      schema:
        $ref: "#/components/schemas/LaunchTemplateRequest"
# LaunchTemplateRequest（现网）:
#   idempotency_key: string | null (max 128)
#   name: string | null (max 200)
```

扩展后：

```yaml
components:
  schemas:
    LaunchTemplateRequest:
      type: object
      properties:
        idempotency_key:
          type: [string, "null"]
          maxLength: 128
        name:
          type: [string, "null"]
          maxLength: 200
        asset_override:
          $ref: "#/components/schemas/AssetOverrideRequest"
        lineage:
          $ref: "#/components/schemas/LaunchLineageRequest"
      additionalProperties: false

    AssetOverrideRequest:
      type: object
      description: >
        Optional. Whole-field replacement semantics: a field that is absent
        inherits the template; a field that is present replaces the template
        value entirely (no merging). The console one-click launch never sends
        this object.
      properties:
        repo_url:
          type: string
          description: >
            Default: the template's asset source. When present, the platform
            resolves the source by exact repo_url match among registered
            AgentAssetSource rows of the same tenant; credentials and scan
            paths follow the matched source.
        ref:
          type: string
          maxLength: 200
          description: Display/audit only. Never authoritative.
        commit:
          type: string
          pattern: "^[0-9a-f]{40}$"
          description: >
            Authoritative identity. The platform resolves this commit in the
            source repository and MUST assert
            resolved_commit == commit; a mismatch fails the launch.
        asset_paths:
          type: array
          items: { type: string, maxLength: 500 }
        asset_types:
          type: object
          additionalProperties: { type: string }

    LaunchLineageRequest:
      type: object
      description: >
        Optional provenance from the caller (harness-evolution). Stored on the
        eval run and echoed in results; it never participates in evaluation
        semantics.
      properties:
        evolutionRunId: { type: string, maxLength: 64 }
        evolutionRoundId: { type: string, maxLength: 64 }
        roundSeq: { type: integer, minimum: 1 }
        parentCommit: { type: string, maxLength: 40 }
```

### 2.2 202 响应（扩展前 → 扩展后）

扩展后新增两个字段：

```yaml
components:
  schemas:
    LaunchAcceptedResponse:
      type: object
      required: [run_id, template_id, status, status_url, result_url, created, asset]
      properties:
        run_id: { type: string }
        template_id: { type: string }
        template_version: { type: integer }
        status: { type: string }          # preparing | queued | running（沿用现网）
        status_url: { type: string }
        result_url: { type: string }
        created: { type: boolean }
        asset:
          $ref: "#/components/schemas/RunAssetIdentity"
        warnings:
          type: array
          items: { type: string }
          description: >
            Present only when asset_override was provided while the template
            enables self-evolution reporting; informational, never blocking.

    RunAssetIdentity:
      type: object
      required: [resolved_commit, asset_snapshot_id]
      properties:
        repo_url: { type: string }
        requested_ref: { type: [string, "null"] }
        requested_commit: { type: [string, "null"] }
        resolved_commit:
          type: string
          description: >
            The commit the evaluation is pinned to. When requested_commit was
            provided, resolved_commit == requested_commit is enforced at launch.
        asset_snapshot_id: { type: string }
        bundle_digest: { type: [string, "null"] }
```

控制台一键启动（不带 `asset_override`）的 202 响应**不含** `asset` 与 `warnings`，与现网逐字节一致；`asset` 在 schema 中非 required 于「无覆盖」场景，实现可用「仅带覆盖时输出」表达（或在 OpenAPI 中声明 `asset` 可空），以实现 PR 为准。

### 2.3 新增 422 错误码（共享错误信封不变）

错误信封沿用现网 `{"error": {"code", "message", "request_id", ...}}`：

```yaml
components:
  schemas:
    LaunchErrorCodes:
      type: string
      enum:
        - template_not_found            # 404（既有）
        - template_disabled             # 409（既有）
        - idempotency_conflict          # 409（既有）
        - template_reference_invalid    # 422（既有）
        - candidate_commit_mismatch     # 422（新增）
        - asset_source_unresolved       # 422（新增）
        - asset_ref_unresolved          # 422（新增）
        - asset_selection_empty         # 422（既有码，launch 场景复用）
        - asset_path_not_found          # 422（既有码，launch 场景复用）
        - image_not_ready               # 409（既有）
        - forbidden                     # 403（既有；服务令牌 scope/白名单不足）
```

新增三个码的触发条件：

| code | HTTP | 触发条件 |
|---|---|---|
| `candidate_commit_mismatch` | 422 | 解析到的 `resolved_commit` 与请求 `commit` 不一致（ref 漂移、对象被替换）。绝不静默评测。 |
| `asset_source_unresolved` | 422 | `asset_override.repo_url` 在同租户已登记资产源中无精确 `repo_url` 匹配，凭据不可确定。 |
| `asset_ref_unresolved` | 422 | commit/ref 在资产源仓库不可解析（仓库不可达、凭据失效、对象不存在）。 |

带 `details`：`{ "field": "asset_override", "requested": ..., "resolved": ... }`，便于 HE 定位。

## 3. `GET /api/v1/eval-runs/{run_id}/status`（响应扩展）

```yaml
EvalRunStatusResponse:
  # 既有字段全部保留（status/terminal/progress/counts/resolve_rate/...）
  properties:
    asset:
      $ref: "#/components/schemas/RunAssetIdentityEcho"
    # RunAssetIdentityEcho = { resolved_commit, asset_snapshot_id, requested_ref }
    #   - 仅模板启动且解析过资产的 run 输出；合成评测为 null / 省略
```

## 4. `GET /api/v1/eval-runs/{run_id}/results`（响应扩展）

```yaml
EvalRunResultsResponse:
  # 既有字段全部保留（status/is_final/items/token_summary/...）
  properties:
    asset:
      $ref: "#/components/schemas/RunAssetIdentityEcho"
    lineage:
      type: object
      description: Echoed only when lineage was provided at launch.
      properties:
        evolutionRunId: { type: string }
        evolutionRoundId: { type: string }
        roundSeq: { type: integer }
        parentCommit: { type: [string, "null"] }
```

## 5. 服务令牌端点（新增）

```yaml
paths:
  /api/v1/service-tokens:
    post:
      summary: Create a service token (admin session only)
      security: [{ sessionCookie: [] }]
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [name, scopes]
              properties:
                name: { type: string, maxLength: 100 }
                scopes:
                  type: array
                  items: { type: string, enum: [eval:launch, eval:read] }
                template_allowlist:
                  type: array
                  items: { type: string }     # template_id; empty = all templates
                max_concurrent_runs: { type: [integer, "null"], minimum: 1 }
                daily_budget_cny: { type: [number, "null"], minimum: 0 }
                expires_at: { type: [string, "null"], format: date-time }
      responses:
        "201":
          description: >
            The plaintext token (hev_...) is returned exactly once and never
            stored; only its SHA-256 hash persists.
  /api/v1/service-tokens/{token_id}:
    delete:
      summary: Revoke a service token (admin session only)
      responses: { "204": { description: Revoked } }
  /api/v1/service-tokens:
    get:
      summary: List service tokens (admin session only; masked)
      responses:
        "200":
          description: id, name, scopes, allowlist size, last_used_at, revoked_at; no secret material.
```

对应存储：`service_token(id, tenant_id, name, token_hash, scopes_json, template_allowlist_json, max_concurrent_runs, daily_budget_cny, expires_at, revoked_at, last_used_at, created_by)`，Alembic 迁移一份。认证依赖：`require_session_or_service`（launch=eval:launch；status/results=eval:read）；`created_by` 落 `svc:<name>`，服务令牌发起的 launch 同样写入既有审计与幂等约束（`(tenant_id, created_by, idempotency_key)`）。

## 6. 兼容性说明

- 全部为增量：不删字段、不改既有字段语义、不改既有错误码含义；旧客户端（控制台）零感知。
- `asset` 块只对「模板启动且解析过资产」的 run 输出；合成评测与旧 run 不输出（字段可空或省略，实现 PR 定稿）。
- 幂等键唯一约束仍是 `(tenant_id, created_by, idempotency_key)`；服务令牌主体 `svc:<name>` 与真人会话天然隔离。
- 平台不给 verdict/策略字段（D4）；HE 依据 `results` 的指标项与 `status` 计数自行换算裁决输入。

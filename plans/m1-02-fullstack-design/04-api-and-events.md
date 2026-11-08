# 04 · API 与事件契约

## 1. 通用约定

- 浏览器业务接口前缀 `/api/v1`，Runner 回调前缀 `/runner/v1`，模型网关前缀 `/model-gateway/v1`。
- JSON 字段使用 `snake_case`；时间使用 UTC ISO 8601；ID 使用 UUID/ULID 字符串。
- 列表采用 `items + page_info` 游标分页，原型中的页码由前端根据总数呈现；稳定排序必须带唯一 ID  tie-breaker。
- 创建评测、重试、取消、回调和导入请求支持幂等；写错误不返回堆栈、SQL、密钥或内部 URL。
- 浏览器写操作需要 CSRF Token 与 Origin 检查；Runner 使用按 eval_case_run/attempt  scoped 的短期 Bearer Token。

```json
{
  "error": {
    "code": "connection_provider_mismatch",
    "message": "所选连接不具备竞价 ECS 供给能力",
    "request_id": "req_01...",
    "fields": {"connection_id": "missing ecs:spot"}
  }
}
```

## 2. 浏览器 API 总表

| 域 | 方法与路径 | 用途 |
|---|---|---|
| Auth | `POST /auth/login`、`POST /auth/logout`、`GET /auth/me` | 登录、撤销、当前用户与默认租户 |
| Overview | `GET /overview` | 运行摘要、资源容量、部署健康 |
| Dataset | `GET/POST /datasets`、`GET /datasets/:id` | 目录、创建与详情 |
| Dataset | `GET /datasets/:id/versions`、`GET /dataset-versions/:id/cases` | 版本与用例分页 |
| Import | `POST /dataset-imports`、`GET /dataset-imports/:id`、`POST /dataset-imports/:id/confirm` | 上传/来源、校验进度、生成版本 |
| Metrics | `GET /dataset-versions/:id/metrics` | 该版本的指标定义与口径 |
| Models | `GET/POST /model-configs`、`PATCH /model-configs/:id`、`POST /model-configs/:id/test` | 模型配置与连通测试 |
| Credentials | `POST /credentials/:id/rotate`、`DELETE /credentials/:id` | 再次认证后轮换/删除 |
| Assets | `GET/POST /agent-asset-sources`、`POST /agent-asset-sources/:id/scan` | Git 源与扫描 |
| Assets | `GET /agent-asset-sources/:id/tree`、`POST /agent-assets/resolve` | 文件树、Ref/commit/资产校验 |
| Runtimes | `GET /agent-runtimes` | 可用 Runtime、版本和能力 |
| Connections | `GET/POST /connections`、`POST /connections/:id/test` | 资源连接与只读验证 |
| Pools | `GET/POST /resource-pools`、`GET /resource-pools/:id/runners` | 池、容量与 Runner |
| Pools | `POST /resource-pools/:id/test-provisioning` | 有费用提示的供给测试 |
| Eval | `POST /eval-runs/prepare`、`POST /eval-runs` | 冻结输入与幂等创建 |
| Eval | `GET /eval-runs`、`GET /eval-runs/:id`、`GET /eval-runs/:id/config` | 列表、详情与快照 |
| Cases | `GET /eval-runs/:id/cases`、`GET /eval-case-runs/:id`、`GET /eval-case-runs/:id/tests` | 用例筛选、详情与测试级打点 |
| Actions | `POST /eval-runs/:id/cancel`、`POST /eval-runs/:id/resume-budget`、`POST /eval-case-runs/:id/retry` | 异步取消、确认增加预算并恢复、新 attempt |
| Events | `GET /eval-runs/:id/events`、`GET /eval-runs/:id/events/stream` | 历史、游标与 SSE |
| Results | `GET /eval-runs/:id/results`、`GET /eval-runs/:id/results/trends`、`GET /eval-runs/:id/compare?baseline_run_id=...` | 指标快照、分布、趋势与可替换基线 |
| Trace | `GET /eval-case-runs/:id/trace`、`GET /trace-sessions/:id/spans` | 会话树与 span 分页 |
| Trace | `GET /trace-spans/:id`、`POST /trace-files/:id/download-url` | span 详情与限时原件下载 |
| Delivery | `GET /deployments`、`POST /deployments/:id/rollback` | 部署历史与受控回滚 |

## 3. 创建评测契约

### 3.1 Prepare

```json
{
  "dataset_version_id": "dsv_01...",
  "case_selection": {"mode": "all"},
  "runtime_id": "runtime_claude_code",
  "runtime_version": "2.1.x",
  "asset_source_id": "aas_01...",
  "requested_ref": "main",
  "asset_paths": ["skills/brainstorming", "skills/test-driven-development"],
  "model": {"mode": "existing", "model_config_id": "mdl_01..."},
  "resource_pool_id": "pool_linux_spot",
  "execution": {"concurrency": 8, "case_timeout_seconds": 3600, "max_attempts": 2}
}
```

Prepare 在服务端重新计算权限和容量，解析 commit，冻结资产与任务清单，并返回短期 `preparation_id`、完整摘要和警告。Prepare 不启动云实例，不创建正式 eval_run。

```json
{
  "preparation_id": "prep_01...",
  "expires_at": "2026-11-08T10:15:00Z",
  "resolved_commit": "40-char-commit",
  "case_count": 500,
  "estimated_job_count": 500,
  "warnings": [{"code": "capacity_queue_expected", "online_runners": 4, "requested_concurrency": 8}],
  "snapshot_digest": "sha256:..."
}
```

### 3.2 Create

请求头 `Idempotency-Key: <uuid>`，Body 仅含 `preparation_id` 和可选展示名。服务端校验 preparation 未过期、属于同用户/租户、快照未失效，然后在单事务创建 run/cases/outbox。唯一约束建议为 `(tenant_id, actor_user_id, idempotency_key)`。

冲突响应：

- `409 preparation_expired`：重新 Prepare，不静默使用新 commit。
- `409 configuration_changed`：模型/池/资产已变化，回确认页。
- `422 capacity_invalid`：并发或超时超出数据集/池限制。
- `403 credential_forbidden`：不能引用该凭据。

## 4. 用例查询与结果

`GET /eval-runs/:id/cases` 建议参数：

```text
cursor, limit, q, execution_status, result_status, trace_status,
sort=created_at|duration|status, order=asc|desc
```

响应每行同时包含：

```json
{
  "id": "ecr_01...",
  "case_key": "django__django-11019",
  "execution_status": "succeeded",
  "result_status": "resolved",
  "trace_status": "ready",
  "latest_attempt": 1,
  "duration_ms": 522000,
  "test_summary": {"passed": 118, "failed": 2, "skipped": 1, "error": 0, "f2p_failed": 2, "p2p_failed": 0},
  "metrics": {"resolved": 0, "cost_usd": 0.42},
  "trace_link": "/eval-cases/ecr_01.../trace?attempt=1"
}
```

测试打点接口按 attempt 返回每条测试的 `suite/name/outcome/duration/group/expected/message/log_ref`。其中 group 可表达 SWE-bench 的 F2P/P2P，outcome 同时保留归一化结果与原生状态；完整 stdout/stderr 和失败堆栈通过受权限控制的 OSS 引用读取。

结果接口返回 `metric_definition_version`、公式口径、分母、`is_final` 和 `generated_at`。前端不自行猜测解决率分母。

对比接口不传 baseline 时由服务端自动推荐同数据集、同 Runtime、同模型、同资产快照的最近一次最终评测，并逐级放宽到同数据集/Runtime/模型；响应包含 `baseline_run_id`、`recommendation_reason`、`configuration_differences` 和指标 delta。用户传入 baseline 时校验同租户、可读权限和结果可用性，允许跨配置比较但必须显示差异。

`resume-budget` 请求包含 `additional_budget_usd` 和当前预算版本，要求再次确认并具有写权限。服务端原子增加预算、记录审计、把 `paused_budget` 评测重新放入调度队列；已完成用例不重跑，被预算打断的用例创建新 attempt。

## 5. Runner 回调与上传

| 接口 | 用途 | 幂等键 |
|---|---|---|
| `POST /runner/v1/case-runs/:id/started` | Job 实际开始、Runner/沙箱元数据 | `(attempt, event_id)` |
| `POST /runner/v1/case-runs/:id/heartbeat` | 进度、当前阶段、用量摘要 | `(attempt, sequence)` |
| `POST /runner/v1/case-runs/:id/uploads` | 获取 OSS 限时上传地址和对象键 | `(attempt, artifact_kind, digest)` |
| `POST /runner/v1/case-runs/:id/completed` | 状态、评分清单、轨迹清单与指标摘要 | `(attempt, completion_digest)` |
| `POST /runner/v1/case-runs/:id/failed` | 可重试/不可重试失败与脱敏摘要 | `(attempt, event_id)` |

回调只在 token 的 `run_id/case_run_id/attempt/pool_id` 与当前记录一致时接受。迟到回调若属于旧 attempt，返回 `409 stale_attempt`，保留审计但不覆盖当前状态。

完成回调示例：

```json
{
  "event_id": "01J...",
  "attempt": 1,
  "sequence": 18,
  "execution_status": "succeeded",
  "score_manifest": {"object_key": "...", "sha256": "...", "size": 18340},
  "trace_manifest": {"object_key": "...", "sha256": "...", "size": 901822},
  "summary": {"duration_ms": 522000, "input_tokens": 146820, "output_tokens": 8421}
}
```

OSS 对象键由平台生成，Runner 不能指定任意前缀。上传完成以 digest/size 校验为准；只收到回调但对象不存在时进入 `artifact_missing`，不可把用例标为完整成功。

## 6. SSE 协议

事件 ID 使用数据库单调事件 ID，数据为脱敏 DTO：

```text
id: 91827
event: eval.case.updated
data: {"run_id":"eval_01...","case_run_id":"ecr_01...","attempt":1,"sequence":18,"occurred_at":"..."}
```

事件类型建议：`eval.run.updated`、`eval.case.updated`、`eval.metric.updated`、`trace.ingestion.updated`、`resource.capacity.updated`、`eval.notice`。所有事件只携带变更摘要；客户端收到后使对应 Query 失效或按 DTO 安全合并。

- 首连必须已先取快照，再以快照游标建立 SSE。
- 重连读 `Last-Event-ID`；服务端从 DB 回放，Redis 只负责唤醒。
- 游标过旧或服务不可追时返回 `resync_required`，客户端重新拉快照。
- run 终态不立即关闭全部连接；发送最终事件后允许服务端正常结束，前端停止无意义重连。

## 7. 模型网关内部契约

Runner 的 OpenAI 兼容 `base_url` 指向平台，`Authorization` 使用 case-scoped 短期 token。网关根据 token 绑定模型配置、允许模型名、预算、租户和 attempt，解密真实 Key 后转发流式请求。

网关必须：

- 覆盖 Authorization、不记录请求/响应头中的凭据；
- 对流式响应统计或回填用量，执行超时与取消传播；
- 限制最大上下文、输出、请求速率和累计预算；
- 将模型用量关联到 case_run/attempt，而不是由 Runner 自报作为唯一依据；
- 供应商错误归一化为 `provider_unavailable/rate_limited/authentication_failed/budget_exceeded`，保留脱敏 request ID。

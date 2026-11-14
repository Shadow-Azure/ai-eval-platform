# 05 · 数据模型与迁移

## 1. 设计规则

- PostgreSQL 为唯一业务事实库；SQLAlchemy + Alembic，JSONB 只存弹性属性，不用 JSONB 代替核心关联。
- 所有租户业务表包含 `tenant_id`；需要复合外键的父表保留 `UNIQUE(tenant_id, id)`，子表外键同时包含租户，防止跨租户引用。
- 状态使用 `TEXT + CHECK` 或应用层枚举，避免 PostgreSQL ENUM 增加值时阻碍在线迁移。
- 所有可重试外部写入具有唯一幂等键；所有时间戳使用 `timestamptz`。
- 密钥只保存 KMS 密文、密钥版本和掩码；密码只保存强哈希，签名会话只保存令牌哈希。
- 大文本、完整报告和原始文件不进入 span/事件表，使用 OSS 对象键、digest、size 与内容类型索引。

## 2. ER 概览

```text
tenant ─ workspace ─ project
  │         │          ├─ dataset ─ dataset_version ─ dataset_case
  │         │          │                    └─ metric_definition
  │         │          ├─ model_config ─ credential
  │         │          ├─ agent_asset_source ─ agent_asset_snapshot ─ agent_asset
  │         │          └─ connection ─ resource_pool ─ runner
  │         │                                      └─ provisioned_instance
  │         │
  │         └─ eval_run ─ eval_case_run ─ eval_case_attempt
  │                 │             │              ├─ trace_session ─ trace_span
  │                 │             │              ├─ trace_file
  │                 │             │              └─ score_artifact
  │                 ├─ eval_event
  │                 └─ metric_result
  └─ user_membership / auth_session / audit_log / outbox / deployment
```

`eval_case_run` 表示“某评测中的一个逻辑用例”，`eval_case_attempt` 表示一次真实执行。轨迹必须关联 attempt；否则重试后新会话会覆盖旧会话，无法诊断失败历史。

## 3. 身份与租户

| 表 | 关键字段 | 约束/索引 |
|---|---|---|
| `tenant` | id, slug, name, status, created_at | slug unique |
| `workspace` | id, tenant_id, slug, name | `(tenant_id,slug)` unique |
| `project` | id, tenant_id, workspace_id, slug, name | 同 workspace 内 slug unique |
| `user` | id, email, password_hash, is_active, created_at | email 全局唯一（MVP） |
| `membership` | tenant_id, user_id, workspace_id, role, is_default | `(tenant_id,user_id)` unique |
| `auth_session` | id, user_id, token_hash, expires_at, revoked_at, last_seen_at | token_hash unique |
| `audit_log` | tenant_id, actor_user_id, action, target_type/id, metadata_json, created_at | `(tenant_id,created_at,id)` |

单租户迁移时创建一条默认 tenant/workspace/project，并将管理员 membership 设为默认。用户 email 全局唯一是 MVP 简化；若未来允许同一邮箱跨完全隔离租户重复注册，需要一并调整登录定位策略。

## 4. 数据集与指标

| 表 | 关键字段 | 说明 |
|---|---|---|
| `dataset` | id, tenant_id, project_id, slug, name, adapter_key, task_family, environment_kind, metadata_json | 稳定目录实体 |
| `dataset_version` | id, tenant_id, dataset_id, version, source_uri, source_revision, adapter_version, case_count, status, task_manifest_key, task_manifest_digest, created_at | 不可变；同来源/修订/适配器版本防重 |
| `dataset_case` | id, tenant_id, dataset_version_id, case_key, ordinal, repo, base_commit, problem_statement, reference_patch_key, test_patch_key, expected_metrics_json, extra_json, environment_image | `(dataset_version_id,case_key)` unique；大补丁可放 OSS；`environment_image` 为 M8-25 的 per-case 运行时镜像，版本级 `dataset_version.environment_image` 仅作单镜像回退 |
| `dataset_import` | id, tenant_id, dataset_id, source_config_json, status, total_count, valid_count, invalid_count, error_report_key, created_by | 一次导入过程 |
| `dataset_image_sources_setting` | id, tenant_id, sources_json, updated_by, created_at, updated_at | M8-26 每个租户的公开镜像源覆盖；空行/空列表表示使用部署默认 `DATASET_IMAGE_REGISTRIES`；`(tenant_id)` unique |
| `metric_definition` | id, tenant_id, dataset_id, metric_key, version, display_name, description, aggregator_key, formula_text, display_kind, unit, direction, denominator_policy, config_json | `(dataset_id,metric_key,version)` unique |
| `dataset_version_metric` | tenant_id, dataset_version_id, metric_definition_id | 固化该版本采用的指标集合 |

`aggregator_key` 只能引用服务端注册表，例如 `ratio`、`sum`、`mean`、`histogram`；`formula_text` 用于解释，不作为 Python/SQL 执行。需要新计算算法时新增聚合器代码和版本。

## 5. 模型、资产与资源

| 表 | 关键字段 | 说明 |
|---|---|---|
| `credential` | id, tenant_id, project_id, kind, provider, ciphertext, kms_key_version, secret_mask, expires_at, last_rotated_at | 模型或资源连接的密文底座，可按 kind 拆分表 |
| `model_config` | id, tenant_id, project_id, credential_id, provider, display_name, base_url, model_name, api_style, limits_json, is_temporary, owner_eval_run_id | 临时配置不进入公共目录 |
| `agent_runtime` | id, runtime_key, version, adapter_version, command_template_json, capabilities_json, trace_format | 平台注册，通常不属租户 |
| `agent_asset_source` | id, tenant_id, project_id, connection_id, repo_url, default_ref, scan_paths_json, selection_mode, runtime_prerequisites_json, load_test_json, last_scanned_commit | Git 源配置；CLI 生成型源记录隔离加载测试结果 |
| `agent_asset_snapshot` | id, tenant_id, source_id, requested_ref, resolved_commit, tree_digest, bundle_key, bundle_digest, created_by | 一次不可变快照 |
| `agent_asset` | id, tenant_id, snapshot_id, asset_type, name, path, content_digest, metadata_json | `(snapshot_id,asset_type,path)` unique |
| `connection` | id, tenant_id, credential_id, provider, region, capabilities_json, status, last_verified_at | 资源连接能力清单 |
| `resource_pool` | id, tenant_id, project_id, connection_id, name, provider_key, runner_group, labels_json, min_capacity, max_capacity, instance_config_json, status | 池配置与供给模式 |
| `runner` | id, tenant_id, resource_pool_id, provider_runner_id, name, labels_json, status, busy_case_attempt_id, last_heartbeat_at, registered_at, deregistered_at | `(resource_pool_id,provider_runner_id)` unique |
| `provisioned_instance` | id, tenant_id, resource_pool_id, provider_instance_id, runner_id, status, spot_strategy, created_at, ready_at, released_at, cost_json | 云实例生命周期 |

`runner.busy_case_attempt_id` 是观测信息，不作为调度锁；真正分配仍以 GitHub Job 和平台 attempt 状态为准。

## 6. 评测与事件

### 6.1 `eval_run`

```text
id, tenant_id, project_id, name, status,
dataset_version_id, runtime_id, runtime_version,
asset_snapshot_id, model_snapshot_json, credential_id, credential_mode,
resource_pool_id, resource_pool_snapshot_json,
requested_concurrency, effective_concurrency, shard_size,
model_budget_usd, budget_spent_usd, budget_version, pause_reason,
case_timeout_seconds, max_attempts, max_turns,
metric_schema_json, task_manifest_digest, scoring_image_digest,
total_cases, queued_count, running_count, succeeded_count, failed_count,
resolved_count, unresolved_count, trace_pending_count,
idempotency_key, created_by, created_at, started_at, finished_at, version
```

聚合计数可以由事件增量维护并定期重算校正；页面权威结果以事务内重算或一致性快照为准，不能依赖前端累加。

### 6.2 `eval_case_run`

```text
id, tenant_id, eval_run_id, dataset_case_id, case_key, ordinal,
execution_status, result_status, trace_status, latest_attempt_no,
duration_ms, metrics_json, score_report_key, error_code, error_summary,
created_at, updated_at, version
UNIQUE(eval_run_id, dataset_case_id)
UNIQUE(eval_run_id, case_key)
INDEX(eval_run_id, execution_status, result_status, ordinal)
```

### 6.3 `eval_case_attempt`

```text
id, tenant_id, eval_case_run_id, attempt_no,
status, phase, dispatch_status, shard_id, github_run_id, github_job_id,
runner_id, resource_pool_id, idempotency_key, dispatch_key,
started_at, heartbeat_at, ended_at, sequence,
short_token_id, sandbox_metadata_json, artifact_manifest_json,
error_code, error_summary, retry_reason
UNIQUE(eval_case_run_id, attempt_no)
UNIQUE(tenant_id, dispatch_key)
INDEX(status, heartbeat_at)
```

### 6.4 `eval_event` 与 `outbox`

```text
eval_event: id BIGSERIAL, tenant_id, eval_run_id, eval_case_run_id,
            attempt_no, sequence, event_type, payload_json, occurred_at
outbox:     id, tenant_id, topic, aggregate_type, aggregate_id,
            operation, payload_json, status, attempts, available_at, created_at
UNIQUE(eval_run_id, id) -- 游标读取与租户边界
UNIQUE(eval_case_run_id, attempt_no, sequence)
```

`eval_event` 是页面历史与 SSE 回放来源，payload 只放可展示且脱敏的摘要。调度 outbox 可复用同一机制或独立表，但 dispatch payload 与 UI 事件权限不同，不能直接把内部 outbox 全量暴露给前端。

## 7. 结果、轨迹与交付

| 表 | 关键字段 | 说明 |
|---|---|---|
| `metric_result` | tenant_id, eval_run_id, metric_definition_id, value_json, numerator, denominator, is_final, generated_at | `(eval_run_id,metric_definition_id,is_final)` 管理快照 |
| `score_artifact` | tenant_id, eval_case_attempt_id, object_key, sha256, size, parser_version, parsed_at, parse_error | 评分原件与解析状态 |
| `test_case_result` | id, tenant_id, eval_case_attempt_id, suite_name, test_name, outcome, native_status, test_group, expected, duration_ms, file_path, line_no, failure_message, log_object_key, log_range_json, extra_json | 每条测试的完整打点；group 可表达 F2P/P2P，原始日志外置 OSS |
| `trace_file` | id, tenant_id, eval_case_attempt_id, runtime_id, native_session_id, object_key, sha256, size, format, format_version, status, parser_version, error_summary | 原件幂等与重放 |
| `trace_session` | id, tenant_id, eval_case_attempt_id, root_session_id, parent_session_id, native_session_id, agent_id, agent_name, runtime_key, status, started_at, ended_at, metadata_json, relation_evidence_json | 主/子会话树 |
| `trace_span` | id, tenant_id, trace_session_id, parent_span_id, native_event_id, ordinal, name, kind, status, started_at, ended_at, duration_ms, input_tokens, output_tokens, cost_usd, attributes_json, content_preview, content_object_key, content_digest | waterfall 与属性 |
| `deployment` | id, environment, git_commit, image_digest, migration_revision, status, triggered_by, started_at, finished_at, rollback_of_id | 交付历史 |

索引建议：

```text
trace_file:   UNIQUE(eval_case_attempt_id, sha256, parser_version)
trace_session: UNIQUE(eval_case_attempt_id, runtime_key, native_session_id)
trace_span:   UNIQUE(trace_session_id, native_event_id)
trace_span:   INDEX(trace_session_id, ordinal), INDEX(parent_span_id)
metric_result: INDEX(eval_run_id, is_final)
test_case_result: INDEX(eval_case_attempt_id, outcome, test_group)
test_case_result: UNIQUE(eval_case_attempt_id, suite_name, test_name, test_group)
```

`native_event_id` 不足一行一 ID 的 Runtime，由适配器用“文件 digest + 行号/路径 + 类型”生成稳定 ID。解析升级重放时在同一事务删除旧派生 span/session 并插入新版本，trace_file 和 OSS 原件不删除。

## 8. 数据保留与删除

- 已确认首轮全部长期保留：评测配置、指标、审计、归一化元数据、原始轨迹、完整工具输出、测试报告、补丁与产物均不自动清理。
- 保留策略仍通过独立生命周期服务实现，首轮不配置过期任务；未来增加保留期时不得修改历史对象的可复现标识。
- 删除凭据采用“被引用检查 + 软撤销”；历史评测保留掩码与配置快照，不保留可用明文。
- 临时凭据在评测全部 attempt 终态后撤销并销毁密文；预算暂停属于非终态，临时凭据仍需保留且受预算网关约束。
- 删除数据集版本若已被 eval_run 引用则禁止物理删除，只允许归档。

## 9. 迁移策略

1. 所有迁移在 CI 对空库和上一版本副本执行。
2. 新增列先 nullable/有兼容默认，新增表和索引采用在线方式；应用仍兼容旧结构。
3. 部署新应用并完成 backfill，观察后再发布约束与删除旧列。
4. 迁移 job 使用数据库 advisory lock 和部署 concurrency，失败阻断应用更新。
5. 回滚优先回滚应用镜像；涉及不兼容 Schema 时禁止自动回滚，转入人工修复或向前修复。

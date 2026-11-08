# Runner 任务协议 v1

本目录定义 M2 的 Runner 派发、清单、心跳、上传、完成与失败契约。协议只传不透明 ID、清单地址与 digest；用例内容、模型 Key 和云凭据不进入 GitHub payload。

## 版本

`schema_version` 固定为 `"1.0"`。新增字段先以可选字段进入 v1，破坏性变更发布 v2 并保留 v1 至少一个 milestone。Schema 文件在 `schemas/v1/`，平台 API、GitHub workflow 和本地 fixture 共用同一份。

## 一次分片执行

```text
平台 --repository_dispatch(eval-shard)--> GitHub workflow
workflow --校验 dispatch--> 拉取 manifest --校验 sha256--> 执行用例
        --心跳/上传清单--> 平台
        --完成/失败回调--> 平台（Idempotency-Key = event_id）
```

## 安全边界

- dispatch 只含 `eval_run_id`、`shard_id`、`attempt`、`dispatch_key`、`manifest_url`、`manifest_digest` 和过期时间。
- 平台签发绑定 `eval_run_id / shard_id / attempt / pool_id` 的短期 token；Runner 在执行前校验，不把长期模型 Key 或云 AK 写入任务包。
- 平台生成对象键；Runner 只上报 digest 与 size。平台收到完成回调后仍要校验对象存在。
- 回调按至少一次投递设计，以 `event_id` 和唯一约束幂等；重复 dispatch 由 `dispatch_key` 原子 claim 拦截。

## 参考实现

`runner/` 提供零依赖参考实现与本地验证：`runner/lib/contracts.mjs`（校验）、`runner/lib/auth.mjs`（短期 token）、`runner/lib/dispatch-guard.mjs`（重复派发）、`runner/lib/object-store.mjs`（本地对象存储替身）、`runner/scripts/synthetic-task.mjs`（确定性合成任务）、`runner/workflows/eval-shard.yml`（私有 Runner 仓 workflow 模板）。真实 OSS、OIDC 和平台 API 由 M2-04/M2-05 接入。

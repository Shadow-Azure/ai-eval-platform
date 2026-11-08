# M2-03 · Runner 任务协议与合成任务

> 状态：2026-11-08 实现记录。契约文件在 `runner-contract/`，参考实现在 `runner/`，测试在 `tests/runner/`。

## 1. 为什么需要单独冻结协议

评测平台和 Runner 是两套独立部署：平台在阿里云 / GitHub，Runner 在私有仓库的固定 Linux 机器上。如果两边各自定义 payload，第一次接真实 Runner 时就会出现“平台发了 A、Runner 只认 B”的联调故障。M2-03 先冻结最小协议，并用一个不调用模型的合成任务证明协议可执行。

一句话：**平台只发“去哪个清单取哪一片任务”，不发题目、不发密钥；Runner 只回“我做到哪、产出了什么、成功还是失败”。**

## 2. 六份 Schema

| Schema | 方向 | 关键字段 | 幂等/校验点 |
|---|---|---|---|
| `dispatch` | 平台 → GitHub | eval_run_id、shard_id、attempt、dispatch_key、manifest_url、manifest_digest、expires_at | dispatch_key 唯一；digest 绑定清单 |
| `task-manifest` | 平台 → Runner | callback_base_url、deadline、cases[1..256] | attempt/run/shard 必须与 dispatch 一致 |
| `heartbeat` | Runner → 平台 | event_id、case_run_id、attempt、sequence、phase、progress | sequence 单调；event_id 幂等 |
| `upload-manifest` | Runner → 平台 | objects[artifact_kind, object_key, sha256, size] | 对象键由平台生成；digest/size 校验 |
| `completion` | Runner → 平台 | execution_status、result_status、score/trace manifest、summary | event_id 幂等；对象存在性由平台复核 |
| `failure` | Runner → 平台 | error_code、retryable、error_summary、phase | retryable 决定是否新 attempt |

版本固定为 `"1.0"`；新增字段先可选，破坏性变更发 v2。

## 3. 执行状态机

```text
dispatch 到达
  → 校验 dispatch schema / 过期时间
  → 校验短期 token（eval_run/shard/attempt 绑定）
  → 拉取 manifest，校验 sha256 与 schema
  → 校验 manifest 与 dispatch 的 run/shard/attempt 一致
  → 原子 claim dispatch_key（重复则退出，不执行）
  → 逐用例：心跳 → 生成确定性 score/trace → 写对象 → 上传清单 → 完成回调
  → 全部成功：标记 claim 完成
  → 回调失败：retryable 重试；永久失败写 failure；409 stale 直接停止
```

## 4. 重复派发与回调幂等

- `dispatch-guard.mjs` 用 `mkdir` 的原子性 claim `dispatch_key`；平台数据库的 `(tenant_id, dispatch_key)` 唯一约束是最终底线。
- `callback-client.mjs` 以 `event_id` 作为 `Idempotency-Key`，网络错误 / 429 / 5xx 有界退避重试，其他 4xx 不重试。
- 409 `stale_attempt` 不重试、也不写 failure，避免旧 attempt 覆盖当前状态。

## 5. Token 边界

参考实现用 HMAC-SHA256 签发 `v1.<claims>.<sig>`，claims 绑定 `eval_run_id / shard_id / attempt / pool_id / exp`。真实平台应由 OIDC 或短期 scoped token 签发，Runner 只持有验证材料，不保存长期模型 Key 或云 AK。workflow 模板里的 `RUNNER_TASK_SECRET` 只用于本地契约演示，M2-04 会替换。

## 6. 合成任务

`runner/scripts/synthetic-task.mjs` 不调用模型：对每个 `case_key` 用 `sha256(dispatch_key + case_key)` 生成稳定数值，输出固定 JSON score 与 NDJSON trace，写入本地 content-addressed store，再按 `upload` / `completion` schema 回调。相同输入重复运行得到逐字节相同的对象；不同 dispatch_key 得到不同数值，便于验证“不是时间/随机数驱动”。

## 7. 测试矩阵

| 场景 | 断言 |
|---|---|
| Schema 正例/反例 | 6 类 payload 正例通过；缺 digest、attempt=0、负 sequence、坏 sha、未知枚举、额外字段被拒绝 |
| 成功闭环 | 2 个用例、4 个对象、2 次 completion；对象 sha256 与内容一致 |
| 重复 dispatch | 第二次只输出 duplicate，completion 次数不增加 |
| digest 不匹配 | 执行前退出，无 POST、无对象 |
| token 不匹配 | 执行前退出，未拉取 manifest |
| manifest attempt 不匹配 | 执行前退出，无 POST |
| 可重试回调失败 | 500/500/200，同一 event_id 重试 |
| 永久失败 | 400 不重试，failure.retryable=false |
| 409 stale | 只发一次 completion，不写 failure |
| 确定性 | 两次独立运行 score/trace 逐字节一致 |
| workflow 模板 | 引用同一 contract 脚本，无密钥模式 |

## 8. 未做与后续

- 真实 OSS 上传、OIDC 换 token、平台回调鉴权与对象存在性校验在 M2-04/M2-05 接入。
- workflow 模板当前是单 job；M2-05 按分片/用例 matrix 扩展。
- 本地测试命令：`node --test tests/runner/*.test.mjs`（24 个用例）。M2-02 会把它接进 CI。

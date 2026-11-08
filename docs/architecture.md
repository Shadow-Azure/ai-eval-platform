# 架构地图

[English](architecture.en.md) | 中文

当前仓库处于评测平台执行底座落地阶段。M2 冻结 Runner 任务协议并建立控制面、数据库与交付流水线；M3 开始接入数据集注册、模型网关、Runtime 适配与评分。

## 组成

| 区域 | 职责 | 边界 |
|---|---|---|
| `docs/` | 当前事实、开发方法和双语契约 | 不存路线图与决策辩论 |
| `.agents/flow/` | roadmap、milestone、issue 状态机与验收边界 | 只保存流程状态，不承载实现细节 |
| `.agents/skills/` | 从 StudyWiki 同步的任务技能与模板 | 不参与本仓双语配对 |
| `scripts/` | 文档与流程机械门禁 | 不承担产品运行时 |
| `.github/workflows/` | 文档和流程的 CI 裁决 | 无外部发布流程 |
| `backend/` | 控制面 API、数据模型、迁移与 worker | 不承担 Runner 内部执行 |
| `web/` | React 控制台外壳与产品页面 | 不放服务端业务规则 |
| `runner-contract/` | Runner 派发、清单、心跳与回传的版本化 Schema | 只定义契约，不含平台服务 |
| `runner/` | 通用 workflow 模板、合成任务与本地契约测试 | 不含数据集或 Agent 特例 |

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

## Runner 契约

M2 把评测执行与具体数据集、Agent 解耦：平台只通过 repository_dispatch 传递 eval_run、shard、attempt、manifest 地址与 digest，Runner 校验 token、清单和 digest 后执行，并以心跳、上传清单、完成或失败回调回传。`runner-contract/` 保存版本化 Schema，`runner/` 保存通用 workflow 模板、确定性合成任务和本地契约测试；真实 OSS、OIDC 与平台回调由后续 issue 接入。

## 数据集注册表

`backend/` 的 dataset 模块把数据集、不可变版本、用例和指标定义写入 PostgreSQL，并把五文件任务包与 manifest 写入对象存储。`DatasetAdapter` 注册表先实现 SWE-bench：导入逐行校验 JSONL，合法用例进入版本，非法行汇入错误报告；任务包区分 Agent 可见文件与评分专用文件。API 提供数据集、版本、用例、指标和导入进度查询。

## 模型网关

Runner 用短期 token 调用 `/model-gateway/v1`；网关校验租户、模型配置、允许模型、attempt 与预算，解密供应商密钥后转发请求，并按 attempt 记录 token 用量与成本。供应商错误归一化为稳定错误码。

## 扩展位置

- 产品运行时和评测执行器落地后，在本页增加组成、数据流和外部依赖，而不是把设计争论塞入 README。
- 开发任务通过 `.agents/flow/` 的 roadmap、milestone 和 issue 推进，流程状态与改动 scope 以该目录为准。
- 需要平台专属校验时，在 `scripts/` 增加独立叶子并由 `run-gates` 组合，同时在 CI 显式接线。
- 文档量增长后先调整层级和预算，再考虑增加站点投影；当前没有 VitePress 或其他生成站点。

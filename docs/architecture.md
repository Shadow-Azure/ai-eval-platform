# 架构地图

[English](architecture.en.md) | 中文

当前仓库处于评测平台初始化阶段。已有结构只负责工程协作、文档一致性和可复用工作流；评测任务的运行架构、数据模型与外部接口尚未落地。

## 组成

| 区域 | 职责 | 边界 |
|---|---|---|
| `docs/` | 当前事实、开发方法和双语契约 | 不存路线图与决策辩论 |
| `.agents/flow/` | roadmap、milestone、issue 状态机与验收边界 | 只保存流程状态，不承载实现细节 |
| `.agents/skills/` | 从 StudyWiki 同步的任务技能与模板 | 不参与本仓双语配对 |
| `scripts/` | 文档与流程机械门禁 | 不承担产品运行时 |
| `.github/workflows/` | 文档和流程的 CI 裁决 | 无外部发布流程 |

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

## 扩展位置

- 产品运行时和评测执行器落地后，在本页增加组成、数据流和外部依赖，而不是把设计争论塞入 README。
- 开发任务通过 `.agents/flow/` 的 roadmap、milestone 和 issue 推进，流程状态与改动 scope 以该目录为准。
- 需要平台专属校验时，在 `scripts/` 增加独立叶子并由 `run-gates` 组合，同时在 CI 显式接线。
- 文档量增长后先调整层级和预算，再考虑增加站点投影；当前没有 VitePress 或其他生成站点。

# 冻结 M2 可执行底座契约

[English](m1-03-detailed-design.en.md) | 中文

```yaml flow
kind: issue
milestone: m1
priority: P0
status: ready
scope:
  - .agents/flow/**
  - .agents/flow/issues/m1-03-detailed-design.*
  - .agents/flow/issues/m1-01-eval-architecture.*
  - .agents/flow/issues/m1-eval-architecture.*
  - docs/architecture.*
  - docs/design/eval-platform-detailed-design.*
  - docs/README.*
  - contracts/**
  - plans/m1-02-fullstack-design/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 5
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/5
```

## 背景

`plans/m1-02-fullstack-design/` 已确立为评测平台的实现基线，完整的全平台详细设计不再作为 M2 的前置条件。M1-03 只冻结 M2 控制面、数据库、部署、固定 Runner 和合成执行闭环所需契约，使 M2 的五个 issue 可以直接实现和验收，并把 M3 以后的设计留给后续里程碑按实际证据演进。

## 目标

- 映射 M2 服务、模块、代码路径和依赖方向，明确平台、Dispatcher、固定 Runner、PostgreSQL、Redis 与对象存储的最小边界。
- 冻结 M2 初始数据模型、迁移顺序、租户复合约束和评测、用例、attempt、事件、outbox 的幂等键。
- 冻结 M2 API、SSE 查询、任务清单、心跳、上传、完成和失败回调的机器可读契约及状态迁移。
- 冻结 main 自动迁移部署、健康检查、兼容回滚和固定 Linux Runner 注册、失联恢复的最小操作流程。
- 为 M2-01 至 M2-05 建立契约与验收映射，并把 OIDC、竞价供给、多 Runner、真实 Agent 等事项明确留给 M3 或 M4。

## 验收

- M2 执行契约以中文 base、英文 `.en.md` 和 `.i18n.yaml` 三件套落地，并列入文档索引。
- `contracts/` 提供 M2 OpenAPI 摘要或接口 Schema、任务与回调 JSON Schema，以及实际迁移顺序和部署状态机。
- 每个 M2 issue 都能从验收项追溯到基线文档章节、数据库对象、API 或任务协议和对应自动化测试。
- 基线中影响 M2 的“建议”“默认”和“需技术验证”项均给出冻结值、验证任务或明确的后续 milestone，不把 M3 以后待定项变成 M2 阻塞。
- 契约通过仓库门禁并完成技术评审；本 issue 不包含生产代码，评审后 M2-01 可以直接进入实现。

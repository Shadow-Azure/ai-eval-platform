# 冻结 M2 执行契约

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

`plans/m1-02-fullstack-design/` 是 M1-03 的设计输入，不是正式详细设计或实现基线。M1-03 不再讨论整体架构和技术方案，只把 M2 真正需要的部分整理为可执行契约，使 M2 的五个 issue 可以直接实现、测试和验收；M3 以后的设计留给后续里程碑按实际证据演进。

## 目标

- 以设计输入为起点，冻结 M2 初始数据库 Schema、核心表关系、租户与幂等约束，以及实际迁移顺序。
- 冻结 M2 API 骨架、Runner 任务协议、派发与回调契约，以及对应状态迁移和错误分类。
- 冻结 M2 部署与迁移流程、健康检查和兼容回滚边界，覆盖固定 Linux Runner 的最小接入路径。
- 把影响 M2 的“建议默认值”升级为冻结值或明确的技术验证项，并将 M2-01 至 M2-05 映射到具体契约与验收测试。

## 验收

- 产出一份精简的中文 base、英文 `.en.md` 和 `.i18n.yaml` 正式设计，并列入文档索引，不机械翻译全部 11 篇设计输入。
- `contracts/` 提供 M2 API 骨架、任务与回调 Schema、数据库迁移顺序和部署状态机等机器可读契约。
- 正式设计包含 M2-01 至 M2-05 的追溯矩阵，将每个验收项映射到设计章节、数据库对象、API 或协议以及自动化测试。
- 设计输入的“建议”“默认”和“需技术验证”项形成冻结清单，每项标记为冻结值、M2 验证任务或后续 milestone，不影响 M2 的项不得阻塞实现。
- 契约通过仓库门禁并完成技术评审；本 issue 不包含生产代码，评审后 M2-01 可以直接进入实现。

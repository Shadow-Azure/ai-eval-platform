# 编写评测平台详细设计文档

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
  - reports/**
adr: []
github:
  number: 5
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/5
```

## 背景

定稿架构回答了系统做什么以及主要技术选型，但尚未把服务边界、数据表、接口契约、状态机、任务协议和测试策略展开到可实现粒度。开发前需要一份与架构方案及原型一致的工程基线，作为拆分实现任务和评审代码的依据。

## 目标

- 明确服务与模块边界、代码组织方式、依赖方向，以及平台、调度器、Runner、对象存储和 GitHub Actions 的职责。
- 给出 MVP 数据模型、迁移策略、索引和多租户约束，并定义核心 API、事件、错误码与 SSE 协议。
- 细化评测与用例状态机、并发调度、重试幂等、超时回收、回调鉴权和短期凭据流转。
- 定义五文件任务包、数据集与执行适配器接口、会话轨迹归一化模型及 OSS 目录约定。
- 覆盖前端路由与组件边界、测试分层、可观测性、安全边界、部署回滚与 CI/CD 实现方案。

## 验收

- 详细设计以中文 base、英文 `.en.md` 和 `.i18n.yaml` 三件套落地，并列入文档索引。
- 每个 MVP 主流程均能沿页面、API、服务、数据表、任务包和存储对象追溯到明确契约。
- 模块图、数据模型图、关键时序图与状态机完整，接口示例包含成功、失败、鉴权和幂等场景。
- 设计说明给出可执行的迁移顺序、测试矩阵、故障处理与上线回滚步骤，并标记所有待决策项及默认方案。
- 文档通过仓库门禁并完成技术评审，可以直接拆分为实现 issue；本 issue 不包含生产代码。

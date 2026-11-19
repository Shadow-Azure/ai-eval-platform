# [m8-53] Token 统计补齐 cache 输入与构成拆分

[English](m8-53-cache-token-accounting.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-53-cache-token-accounting.*
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
  - plans/m1-02-fullstack-design/08-trace-normalization.md
  - plans/2026-11-19-pr*-review.md
  - prototype/**
  - reports/**
  - backend/app/**
  - tests/backend/**
  - web/src/**
adr: []
github:
  number: 339
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/339
```

## 背景

PR #336 上线后，生产 Token 统计只显示非缓存 input + output，不包含 Claude Code 的 cache token。生产运行 `0a51941d`（6 用例）非缓存输入 1,176,376、输出 425,226，而 `gen_ai.usage.cache_read_input_tokens` 为 47,890,419；远端模型用量通常把 cache 计入 input，因此平台数字看起来明显偏小。另：数据集累计 Token 只覆盖 49/99 次有轨迹评测，卡片没有展示覆盖率。

## 目标

- 把 cache 输入计入用户可见总 Token，同时保留四项拆分：总 Token（含缓存）、cache 输入（read / creation）、非缓存输入、输出。历史 #332 的非缓存字段语义不变，新增字段向后兼容。
- 后端从 LLM span 的 `gen_ai.usage.cache_read_input_tokens` / `cache_creation_input_tokens` 聚合，仍排除 AGENT 汇总 span；同一 SQLAlchemy JSON 表达式适配 SQLite 测试与 PostgreSQL 生产。
- `/cases`、`/evaluations/{id}`、`/results`、数据集、轨迹接口全部返回 cache 字段；Web 在用例明细、结果分析、数据集详情/列表、会话轨迹统一口径显示。
- 数据集累计明确显示“有轨迹评测 N/M 次”，缺失轨迹的评测不计入平均，也不冒充 0。
- 前端 UI 按 `prototype` 中 issue #339 的原型实现；提交前对照原型与设计文档修正差异。

## 验收

- 总（含缓存）= 非缓存输入 + cache 输入（read + creation）+ 输出，数字可对账；cache 不混入非缓存输入。
- 用例行/汇总条/结果/数据集/轨迹口径一致，数据集累计显示有轨迹覆盖率。
- 后端全量测试（SQLite 与 CI PostgreSQL）、前端 typecheck/build、仓库门禁通过。
- PR 描述包含生产 UI 修改前/修改后截图与冒烟结果，并与原型效果对照。

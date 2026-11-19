# [m8-53] 用例明细与数据集详情补齐 Token 消耗统计

[English](m8-53-token-usage-stats.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-53-token-usage-stats.*
  - prototype/**
  - reports/**
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
  - web/src/**
  - backend/app/**
  - tests/backend/**
  - docs/**
adr: []
github:
  number: 332
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/332
```

## 背景

生产评测详情的“用例明细”只有执行、结果、轨迹、测试打点与耗时，没有 Token；“结果分析”只有解决率类指标；数据集页没有 Token 汇总。轨迹页其实已经保存了真实的单次 attempt token（示例：60.9 万 tokens / ¥2.56），但这些数据没有聚合到用例集与数据集层。用户在 6 个用例的列表里无法直接定位昂贵用例，也无法按数据集做成本回归。来源：2026-11-19 飞书请求（附生产用例明细截图）。

## 目标

- 用例集（一次评测的用例明细）：每个用例展示 Token 与成本，表格上方给出该用例集的汇总（总 Token、输入/输出、平均每用例、总成本、Token 覆盖率），并支持按 Token 排序。
- 数据集级：数据集详情与数据集目录展示累计 Token、平均每用例、平均每次评测、累计成本；用例预览展示每用例平均 Token，并支持按版本查看 Token 趋势。
- 统计口径：Token = `input_tokens + output_tokens`；缓存读写 token 单独展示，不混入合计；成本币种必须与 trace `cost-state` 的实际单位一致（当前存 USD、页面按 CNY 展示，需要修正或明确标注）。
- 原型先行：本 issue 先修改 `prototype/` 并附修改前/后的截图，生产实现随数据链路一起落地。

## 验收

- 原型：用例明细出现用例集 Token 汇总与 Token/成本列；数据集详情出现数据集级 Token 卡片与用例预览 Token 列；数据集目录卡片出现累计 Token；修改前/后截图附在本 issue 与飞书。
- 生产：结果分析、用例明细、数据集页展示与原型一致；同一 run 的页面合计与 trace 聚合一致（允许 ±1 舍入差异）。
- 排序、筛选与 CSV 导出包含 Token 与成本字段。
- `npm test`、`npm run verify:docs`、`npm run verify:flow` 通过，Web 侧 `npm run typecheck` 通过。

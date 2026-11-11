# 实现结果基线与配置差异对比

[English](m5-06-baseline-comparison.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P1
status: done
scope:
  - .agents/flow/issues/m5-06-baseline-comparison.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
  - plans/**
  - .agents/flow/issues/m5-09-optional-evaluation-assets.*
  - .agents/flow/issues/m5-10-unified-cny-billing.*
  - .agents/flow/issues/m5-11-runner-tarball-proxy.*
  - .agents/flow/issues/m5-12-eval-shard-scripts-bundle.*
adr: []
github:
  number: 88
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/88
```

## 背景

结果页目前只展示单次评测快照，用户无法判断模型、Runtime、资产、数据集版本或执行策略变化带来的影响。目标产品需要自动推荐可比基线，并明确展示指标差值和配置差异，同时避免把不可比评测拼在一起。

## 目标

- 定义可比条件，至少覆盖数据集版本或用例集合、指标口径、Runtime、模型和资源池等关键配置，并给出推荐为基线的理由。
- 自动推荐最近的可比最终评测，同时允许用户手动选择基线，并明确标识不可比字段。
- 在结果页展示主指标、辅助指标和成本效率的差值，未知指标渲染器安全降级。
- 展示当前评测与基线的配置差异，包括 Agent 资产 commit、模型、Runtime、并发、超时、重试和资源池。

## 验收

- 有可比历史时自动推荐基线并显示理由；没有可比历史时展示空态而不是选择任意评测。
- 手动切换基线后指标差值和配置差异同步更新，不可比条件清晰可见，不使用不同分母或不同指标版本直接比较。
- 数值、百分比、成本、时长和分布指标按各自展示形态计算差值，未知指标不阻断页面。
- 基线选择、推荐规则、不可比场景、空态和配置差异有 API 与前端测试。

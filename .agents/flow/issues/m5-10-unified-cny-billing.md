# 统一前后端费用单位为 CNY

[English](m5-10-unified-cny-billing.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P1
status: done
scope:
  - .agents/flow/issues/m5-10-unified-cny-billing.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - plans/**
adr: []
github:
  number: 124
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/124
```

## 背景

平台费用展示与存储混用币种。阿里云竞价原始报价是 CNY/小时，后端按固定 7.2 汇率换算成 USD 后再与阈值比较、落库并返回，前端只显示 USD；模型价格、模型用量成本、评测预算与成本估算也以 USD 记账。用户需要自己对竞价价格和阈值做二次换算，成本估算又把模型与机时费用混在同一个 USD 总额里。

## 目标

- 平台费用统一以 CNY 表示，前后端的字段、存储、展示与输入保持一致，不再保留运行时汇率换算。
- 新增统一金额类，集中处理费用金额的构造、量化、序列化与展示，避免各模块各自格式化。
- 一次性迁移历史 USD 数据，换算前后的实际金额保持等价。
- 资源池、模型、评测与结果页面统一用 CNY 标注单位和金额。

## 验收

- 资源池当前价格、阈值、容量事件与供给测试统一显示并输入 CNY/小时，阿里云原始报价直接使用，不再除以汇率。
- 模型价格、模型用量成本、评测预算、成本估算与指标货币类型统一为 CNY。
- 迁移脚本按文档化的 7.2 汇率把历史 USD 金额换算为 CNY，升级后数值等价。
- 后端 pytest、前端 typecheck/build、文档与流程门禁全部通过。

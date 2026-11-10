# 实现资源连接与资源池产品页面

[English](m5-03-resource-pools-ui.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P0
status: ready
scope:
  - .agents/flow/issues/m5-03-resource-pools-ui.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
  - reports/**
  - plans/**
adr: []
github:
  number: 85
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/85
```

## 背景

运营页只能只读展示资源池与 Runner，`/resources` 仍是占位页。M4 将建立 resource connection、池配置和供给 API，但用户需要能在产品中验证连接、创建池、查看容量和解释排队原因，才能不依赖命令行完成评测启动。

## 目标

- 实现资源连接列表与新增流程，展示供应商、区域、能力、验证状态、最近验证时间和脱敏凭据。
- 实现资源池创建与编辑，只有兼容当前连接的供给模式可选，能力不匹配时在提交前说明原因。
- 展示池容量上下限、在线与忙碌 Runner、当前价格、阈值、最近心跳、下次检查时间和排队原因。
- 提供需要费用提示和二次确认的供给测试入口，记录审计结果，并完整处理空态、失败和无权限状态。

## 验收

- 用户可以从页面创建资源连接和资源池，连接能力与供给模式不匹配时 API 和页面都拒绝创建并给出明确原因。
- 资源池页能够区分在线、忙碌、离线和扩容中的 Runner，并解释因价格、库存或容量导致的排队。
- 供给测试有明确费用提示和二次确认，成功后展示创建实例到注册 Runner 的过程，失败时保留可诊断结果。
- 页面不显示云密钥或完整连接串，管理员操作写入审计，普通成员只能查看授权信息。

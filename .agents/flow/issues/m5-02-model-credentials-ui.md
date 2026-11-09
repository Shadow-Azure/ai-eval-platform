# 实现模型凭据与配置产品页面

[English](m5-02-model-credentials-ui.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P0
status: ready
scope:
  - .agents/flow/issues/m5-02-model-credentials-ui.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 84
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/84
```

## 背景

模型凭据、模型配置、Key 加密、掩码、轮换、撤销和连通性测试已有完整 API，但导航中的「模型凭据」仍是占位页。主路径因此仍需通过 seed 或接口手工配置，无法满足 M5「不再依赖 seed 数据或命令行」的目标。

## 目标

- 实现模型配置列表与详情，展示服务商、端点、模型名、API 协议、价格配置、凭据掩码和最近使用情况。
- 实现新增与编辑流程，可绑定已有凭据或本次新建凭据，Key 只写不读，不进入 URL、本地存储或查询缓存。
- 实现凭据轮换、撤销和模型端点连通性测试，失败时展示稳定的字段级错误与诊断信息。
- 保持管理员写权限和普通成员只读边界，并复用现有加密、模型网关和审计能力。

## 验收

- 管理员可以从页面创建模型配置、测试端点、轮换凭据并撤销凭据，普通成员不能执行写操作。
- 页面与 API 永远不回显明文 Key，浏览器刷新、前进后退和查询缓存中都不保留秘密。
- 配置变更后，发起评测向导立即看到新的可用模型；失效或撤销的凭据在预检中形成明确阻塞项。
- 关键表单、权限、空态、错误态、轮换和撤销均有组件测试或 API 集成测试。

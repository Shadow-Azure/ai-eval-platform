# 建立资源连接、池配置与容量控制

[English](m4-03-resource-connection-capacity-control.en.md) | 中文

```yaml flow
kind: issue
milestone: m4
priority: P0
status: done
scope:
  - .agents/flow/issues/m4-03-resource-connection-capacity-control.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - tests/**
  - docs/**
  - plans/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 80
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/80
```

## 背景

现有资源池只有 provider key、标签和状态，能够承接固定 Runner，却不能表达云账号连接、供应商能力、供给模式、容量上下限和价格策略。`ProvisionedInstance` 虽有骨架，但没有可替换的供给器接口、容量控制器或兼容性校验，M5 无法安全构建资源池页面。

## 目标

- 新增租户级 resource connection，复用现有加密 Credential 的 `kind=resource`，记录供应商、区域、能力清单、验证状态和最近验证时间。
- 扩展资源池配置，建立 connection、供给模式、最小与最大容量、实例参数、价格阈值和标签之间的受约束关系。
- 定义统一 provisioner 接口，覆盖连接能力校验、创建、释放和健康查询，并通过 fake provider 实现可重复的容量控制测试。
- 根据排队用例、空闲 Runner 和启动中实例计算容量缺口，发布扩容、缩容和价格等待原因供调度器与运营页面消费。

## 验收

- 不兼容的 connection 与供给模式组合在创建资源池前返回稳定错误码，不创建池或实例。
- fake provider 能按容量缺口扩容、在空闲后先注销 Runner 再释放实例，并覆盖创建失败、库存不足和释放失败。
- 价格超过阈值时任务保持排队，资源池状态和事件包含当前价格、阈值、检查时间与下一次检查时间。
- 资源连接密钥不出现在 API 响应、日志、事件或 Runner 可访问的数据中，迁移与能力校验测试通过。

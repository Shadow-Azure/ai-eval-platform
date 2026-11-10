# 接入阿里云竞价 ECS 与临时 Runner

[English](m4-04-aliyun-spot-provisioner.en.md) | 中文

```yaml flow
kind: issue
milestone: m4
priority: P0
status: ready
scope:
  - .agents/flow/issues/m4-04-aliyun-spot-provisioner.*
  - .agents/flow/milestones/m4-concurrency-and-spot-pool.*
  - backend/**
  - runner/**
  - infra/**
  - deploy/**
  - .github/workflows/**
  - tests/**
  - docs/**
  - plans/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 81
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/81
```

## 背景

固定 Linux Runner 只能验证执行链路，无法按排队负载弹性扩容。目标架构要求以阿里云竞价 ECS 提供临时 Runner，但当前没有云端价格与库存查询、实例生命周期、一次性 Runner 注册或中断恢复实现，空载时也无法自动缩容。

## 目标

- 实现阿里云 ECS 竞价供给器，覆盖价格与库存查询、实例创建、启动等待、释放和中断检测。
- 为临时 Runner 建立一次性注册流程，注册信息只在实例初始化阶段可用，Runner 以 ephemeral 模式绑定资源池标签与唯一名称。
- 将供给器接入 M4-03 的容量控制器，按缺口分批扩容，并在没有活动任务时注销 Runner 后释放实例。
- 将竞价中断、心跳丢失、创建超时和库存不足统一交给对账与重试流程处理，不产生重复结果。

## 验收

- 受控云环境能够创建一个竞价实例、注册临时 Runner、执行小批量任务，并在任务完成后完成缩容和释放。
- 当前价格超过阈值、库存不足或创建失败时不会误标用例失败，评测保持排队并展示可诊断的资源池原因。
- 竞价实例中断或 Runner 无预警失联后，相关 attempt 可被识别并重试，旧结果不被覆盖。
- 长期云凭据、模型密钥和一次性注册信息不进入日志、评测产物或 Runner 持久磁盘，provider mock 与真实冒烟报告均有证据。

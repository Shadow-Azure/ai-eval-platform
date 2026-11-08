# 接入阿里云竞价资源池的供给与回收

[English](m4-02-spot-pool-provisioning.en.md) | 中文

```yaml flow
kind: issue
milestone: m4
priority: P0
status: backlog
scope:
  - .agents/flow/**
  - backend/**
  - runner/**
  - runner-contract/**
  - tests/**
  - deploy/**
  - .github/workflows/**
  - docs/**
  - plans/**
  - reports/**
  - pyproject.toml
  - docker-compose.yml
adr: []
github:
  number: 29
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/29
```

## 背景

固定 Runner 只能验证链路，弹性评测需要按队列缺口创建阿里云竞价实例并注册临时 Runner，再在空闲或中断时安全回收。

## 目标

- 建立 connection 表与 API，校验 ECS 供给能力，并维护池的最小、最大容量和实例配置。
- 实现竞价 ECS 供给：价格阈值、库存与创建失败退避、JIT 临时 Runner 注册、空闲缩容和中断回收。
- 用 workflow_job 事件与周期性队列查询双重驱动扩缩容，webhook 丢失不能导致永不扩容。
- 只在实例初始化阶段注入一次性注册信息，不暴露长期云凭据和模型 Key。

## 验收

- 资源池可扩容到上限并注册临时 Runner，空闲后先注销再释放实例。
- 当前价格高于阈值时不创建实例，评测保持排队并展示价格、阈值和下次检查时间。
- 竞价中断和心跳丢失被标记并按重试策略处理，不生成重复结果。
- 创建失败、库存不足和释放异常由 mock 客户端测试覆盖。

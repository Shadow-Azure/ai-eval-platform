# 建立控制面与数据库底座

[English](m2-01-control-plane-foundation.en.md) | 中文

```yaml flow
kind: issue
milestone: m2
priority: P0
status: done
scope:
  - .agents/flow/issues/m2-01-control-plane-foundation.*
  - backend/**
  - web/**
  - plans/**
  - reports/**
  - tests/**
  - pyproject.toml
  - package.json
  - package-lock.json
  - docker-compose.yml
adr: []
github:
  number: 11
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/11
```

## 背景

评测平台目前只有架构、原型与设计稿，尚无能够承接执行回调和持久化状态的运行服务。M2 需要先建立最小控制面与数据库底座，让后续部署、Runner 和执行协议有真实落点。

## 目标

- 建立 FastAPI 模块化单体、Web 最小壳、SQLAlchemy 与 Alembic 的工程骨架。
- 建立默认 tenant/workspace/project，并落地评测、用例、attempt、事件、outbox 与资源基础表。
- 提供本地 PostgreSQL、Redis 和 S3 兼容对象存储的 Compose 开发环境。
- 为迁移、唯一约束、租户作用域和状态持久化建立自动化测试。

## 验收

- 空库可一次迁移到最新版本，重复执行和失败迁移都有明确结果。
- 本地 Compose 可启动 API、Worker、Web、PostgreSQL、Redis 与对象存储，并暴露健康检查。
- 核心表包含幂等键、租户边界和必要索引，服务重启后数据与状态不丢失。
- 自动化测试覆盖迁移、复合约束、默认租户创建和最小 API 健康路径。

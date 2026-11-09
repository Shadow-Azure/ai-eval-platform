# 运营状态页数据库标签页与容量配置

[English](m3-07-ops-database-tabs.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m3-07-ops-database-tabs.*
  - backend/**
  - deploy/**
  - web/**
  - tests/**
  - docs/**
  - .github/workflows/deploy.yml
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 67
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/67
```

## 背景

首个运营状态页把 PostgreSQL 作为唯一数据库区块，Redis 与对象存储只在服务列表里显示一行状态；管理员无法在同一处切换查看两类存储的容量和使用情况。PostgreSQL 的存储容量上限当前显示“未配置”，因为生产库跑在 ECS 系统盘上的 Docker 卷里，PostgreSQL 本身不暴露磁盘配额，部署流程也没有把容量上限传给 API。

## 目标

- 数据库区改为 PostgreSQL / Redis / 对象存储三个可点击切换的标签页，每页展示对应服务的状态、用量与容量信息。
- PostgreSQL 页保留迁移版本、库大小、连接数水位、存储容量水位与表行数；Redis 页展示内存使用/上限、客户端数/上限、版本与探活耗时；对象存储页展示 bucket、端点、探活耗时与状态。
- 新增部署 Secret `DATABASE_STORAGE_LIMIT_BYTES` 的传递：部署工作流把它写入主机 `.env`，Compose 传给 API；未配置时页面明确标注，而不是编造上限。
- 不返回连接串、访问密钥或其它凭据明文；只读成员仍不可见且接口返回 403。

## 验收

- 管理员在 `/operations` 可以点击切换 PostgreSQL、Redis、对象存储三个标签页，各页展示对应状态与容量信息；未配置或探活失败时显示明确原因。
- PostgreSQL 页显示连接数水位和存储容量水位；Redis 页显示已用内存/上限、客户端数/上限；对象存储页显示 bucket、端点与探活结果。
- 部署工作流把 `DATABASE_STORAGE_LIMIT_BYTES` 写入主机 `.env` 并传给 API；在未设置 Secret 时页面仍可正常显示“未配置上限”。
- pytest 覆盖三个数据库区块的状态与容量字段、Redis 未配置/正常探活、对象存储探活；前端 typecheck 与 build 通过。
- PR 附同一视口、同一主题的 before/after 截图，before 为当前单数据库区块页面，after 为标签页版本。

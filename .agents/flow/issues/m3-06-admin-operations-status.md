# 管理员运营状态页

[English](m3-06-admin-operations-status.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m3-06-admin-operations-status.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 61
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/61
```

## 背景

M3 已接入真实数据集、模型网关、执行运行时与账号会话，但控制面只有一个面向评测业务的总览页，管理员无法在一个页面确认当前服务、执行资源和数据库的实际状态。运营需要集中查看这些基础设施信息，并在异常时快速定位是哪一层出了问题。

## 目标

- 新增管理员专属的“运营状态”子页面：普通只读成员在导航中不可见，直接访问显示无权限，后端接口返回 403。
- 服务状态覆盖控制面 API、PostgreSQL、Redis、对象存储、Runner 平面、调度 Worker 与模型网关：已配置项给出探活结果，未配置项明确标注“未启用”，异常项展示可读原因。
- 资源状态展示资源池与 Runner 在线数/最近心跳、评测任务计数、数据集与用例计数、模型配置数量以及调度队列积压。
- 数据库状态展示连通性、迁移版本、数据库大小与关键表行数，不返回连接串、密钥或任何凭据明文。
- 调度 Worker 每轮轮询写入数据库心跳，运营状态据此判断其存活；页面支持手动刷新并定时自动刷新。

## 验收

- 管理员打开 `/operations` 能看到服务、资源与数据库三块现状；只读成员访问页面显示无权限且 `GET /api/v1/operations/status` 返回 403，匿名请求返回 401。
- 服务项覆盖 API、数据库、Redis、对象存储、Runner、Worker 与模型网关，未配置/异常/正常三种状态在页面上可区分并带原因。
- 资源区展示 Runner 与资源池、评测、数据集、模型配置和调度队列计数；数据库区展示迁移版本、大小与关键表行数。
- pytest 覆盖管理员成功、只读成员 403、匿名 401、服务字段、Worker 心跳新鲜/过期以及数据库表行数；前端 typecheck 与 build 通过。
- PR 附同一视口、同一主题的 before/after 截图，before 为未新增该页面时的实际应用状态，after 为新增后的运营状态页。

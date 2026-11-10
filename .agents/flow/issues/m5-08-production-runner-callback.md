# 打通竞价资源池的生产 Runner 回调入口与端到端验证

[English](m5-08-production-runner-callback.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m5-08-production-runner-callback.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - deploy/**
  - .github/**
  - runner-contract/**
  - scripts/doc-budgets.manifest.json
  - plans/**
adr: []
github:
  number: 119
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/119
```

## 背景

M4-03/M4-04 已具备资源连接、竞价供给与 JIT 临时 Runner，M5-03 已交付连接/池页面；但生产部署的 Runner 运行面仍未接通：API 只监听回环地址，安全组未放行 VPC 回调端口，worker 与 api 缺少回调基址，竞价实例取不到 manifest、回传不了结果。供给测试在 Runner 注册前就释放实例，eval-shard 的执行任务又写死默认标签，自定义标签池即使注册了 Runner 也接不到任务。

## 目标

- 在 VPC 内最小暴露 Runner 回调入口，保留固定 Runner 的回环路径，不新增公网入口。
- 把回调基址、Runner 安装包缓存等部署配置持久化到仓库与主机 `.env`，重部署和换机都不丢。
- 让分片 dispatch 与 manifest 冻结池标签，使自定义标签池的执行任务落到对应 Runner。
- 用 5 个用例、并发 5 在真实竞价池上验证创建、注册、执行、回传与回收。

## 任务

| 任务 | 内容 | 归属 |
|---|---|---|
| 部署配置 | compose 增加回环之外的 VPC 绑定与 `RUNNER_CALLBACK_BASE_URL`，deploy.yml 写入主机 `.env` | 代码 |
| 标签调度 | dispatch/manifest 携带 `runs_on`，eval-shard 的 prepare 输出并用于 execute | 代码 |
| 供给测试 | 维持创建验证语义，界面与文档明确该操作不注册 Runner | 文档 |
| 安全组 | 放行 TCP 8000 ← VPC 网段，确认只走私网 | 人工 |
| 密钥注入 | 配置 `API_BIND_ADDRESS`、`RUNNER_CALLBACK_BASE_URL`，可选 Runner 包缓存 URL | 人工 |
| 端到端验证 | 建阿里云连接与竞价池，跑 5 用例并发 5 并保留证据 | 人工 |

## 验收

- 生产 API 在保留回环的同时监听 VPC 地址，8000 只对 VPC 网段开放。
- 竞价池 Runner 能取 manifest 并回传结果；分片任务标签与池注册标签一致。
- 5 用例并发 5 时峰值 running=5、5/5 回传、实例自动释放，证据记录在 PR 描述。
- 部署配置入库，重部署或换机后无需改代码即可恢复回调入口。

## 生产启用清单

- [ ] 安全组入方向放行 TCP 8000，来源为 VPC 网段。
- [ ] GitHub Secrets 设置 `API_BIND_ADDRESS` 与 `RUNNER_CALLBACK_BASE_URL`。
- [ ] 合并本 PR 并确认自动部署成功。
- [ ] 用现有阿里云 AK 建连接与竞价池，跑 5 用例并发 5 并验收。

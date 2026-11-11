# 去掉 eval-shard 的 checkout 依赖：脚本包 + OSS 缓存 + 代理下载

[English](m5-12-eval-shard-scripts-bundle.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P1
status: done
scope:
  - .agents/flow/issues/m5-12-eval-shard-scripts-bundle.*
  - .agents/flow/milestones/m5-product-workflows.*
  - .github/workflows/eval-shard.yml
  - .github/workflows/deploy.yml
  - backend/app/api/runners.py
  - backend/app/config.py
  - backend/app/services/**
  - scripts/**
  - runner/**
  - runner-contract/**
  - tests/**
  - docs/**
  - deploy/**
adr: []
github:
  number: 134
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/134
```

流程编号：M5-12（GitHub #134）

## 背景

生产复测中，eval-shard 的 execute job 在阿里云 spot Runner 上因 `actions/checkout` 失败（run 42940436677）。job 在 `real-task.mjs` 之前失败 → `started` 回调不会发生 → 平台 attempt 一直 queued，直到 3600 秒 `queue_timeout`。prepare 失败会让整个 shard 的用例一起卡住；execute 失败只卡该用例。

PR #133 给 checkout 增加了 3 次步骤级重试，只降低了失败概率；关键路径仍然依赖 GitHub clone，失败后平台仍然没有感知，只能在 3600 秒后收尾。

## 问题现象

- 平台侧 case/attempt 长时间显示“排队中”，GitHub job 已经是 failure；两个界面不一致。
- 生产 run 42940436677：01:38:39Z 派发，01:43:27 执行 job 启动，01:43:33 开始 checkout；01:45:03、01:48:09、01:50:32 三次 fetch 失败（GnuTLS reset、curl 28、packfile），01:50:38 job failed；平台直到 02:38:39Z 才 queue_timeout。
- 平台不监听 workflow run failure；手动重试要求 run 仍为 running，run 已终态时只能新建评测。

## 方案

把 prepare/execute 需要的平台脚本做成内容寻址发布物，Runner 通过平台代理下载，不再 checkout 平台仓库：

1. 发布时打包 `runner/` 与 `runner-contract/schemas/`（`contracts.mjs` 会读取上一级 schema）。
2. 确定性 tar/gzip，计算 md5（并记录 sha256），命名 `eval-runner-scripts-<md5>.tar.gz`。
3. OSS 检查 `runner/scripts/<name>`；存在且大小合理则复用，否则上传并校验。
4. 平台新增 `/runner/v1/scripts/<name>` 代理，从私有 OSS 流式返回；用短期、绑定 eval/shard/attempt 的 token 鉴权。
5. prepare/execute 下载、校验、解包到 `$RUNNER_TEMP/eval-scripts`，从包内路径运行 contracts、plan-cases、real-task。
6. 迁移期保留 checkout fallback：bundle 成功则跳过 checkout；bundle 失败才走 checkout；E2E 通过后删除 fallback。

复用 PR #129 的 deploy OSS 缓存 job 与 PR #132 的固定代理端点。

## 任务

| 任务 | 内容 | Owner |
|---|---|---|
| 发布物 | deploy 加入 bundle 构建/缓存，注入 name/md5；补 runner/runner-contract 触发路径 | Code |
| 代理与鉴权 | scripts 代理、companion 元数据端点、短期 token | Code |
| Workflow | 删除 prepare/execute checkout，下载/校验/解包；保留 fallback | Code |
| 失败感知 | dispatch 预检 bundle；job 失败回调或 workflow_run 轮询 | Code |
| 验证 | 本地确定性/代理测试；阿里云 5 用例 E2E | Human + Code |

## 验收

- 同一源码两次构建 md5 相同；包内含 runner + schemas，解包后 contracts/plan-cases 可运行。
- prepare/execute 不再出现 `actions/checkout`；5 用例并发 5 全部 started/回传。
- bundle 缺失或代理 5xx 能在分钟级暴露，不依赖 3600 秒 queue_timeout。
- bundle 成功时不触发 checkout；fallback 仅在 bundle 失败时触发并留下告警。
- PR #133 仅作为临时止血；最终方案不依赖 GitHub clone。

## 风险与非目标

- 非目标：不改用例目标仓库与 dataset 镜像；不把 OSS 设为公共读。
- 风险：md5 只防误传不防篡改（配合 sha256）；token 过期/scope；版本错位（workflow HEAD vs 部署 commit）；旧 bundle 清理。
- fallback 是迁移期兜底，不是长期架构；bundle 长期不可用时仍会退化回 GitHub clone。

## 参考

- PR #133（checkout 重试，OPEN）
- PR #129（runner 包缓存到 OSS，MERGED）
- PR #132（runner 安装包代理端点，MERGED）
- Issue #119（M5-08 生产 Runner 回调）
- run 42940436677

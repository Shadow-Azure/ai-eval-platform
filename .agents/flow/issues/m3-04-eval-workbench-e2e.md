# 评测工作台 UI 与首个真实用例端到端

[English](m3-04-eval-workbench-e2e.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: done
scope:
  - .agents/flow/issues/m3-04-eval-workbench-e2e.*
  - .agents/flow/milestones/m3-first-real-evaluation.*
  - web/**
  - backend/**
  - deploy/**
  - tests/**
  - reports/**
  - runner/**
  - runner-contract/**
  - .github/workflows/eval-shard.yml
  - .github/workflows/deploy.yml
adr: []
github:
  number: 48
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/48
```

## 背景

M2-05 已打通派发、执行、回传与状态持久化并交付 `/evals` 页面雏形；M3 要在其上补齐数据集导入向导、评测启动向导与结果视图（需求 2/5/7），并把 M2-05 登记的真实 Aliyun OSS 遗留接入（当时以 MinIO 等价物走通 SigV4 链路）。M3 的最终证据是一个真实 SWE-bench 用例经真实模型网关与 Claude Code Runtime 在固定 Runner 上完成执行、评分与结果查询。

## 目标

- 数据集导入三步向导与数据集目录/详情页；评测启动四步向导展示 Ref 解析结果与一致性检查（阻塞项可见）。
- 进度视图：SSE 变更通知 + DB 快照与事件游标恢复（决策 D6），刷新/重连不丢进展不倒退。
- 结果视图：执行状态与解决结果两维度（决策 D5）、测试级打点、score/trace 对象链接。
- 真实 Aliyun OSS 接入：主机 `.env` 注入 OSS/S3 凭据，替换 MinIO 等价物，验证真实上传链路。
- 端到端：一个真实 SWE-bench 用例经模型网关 + Claude Code Runtime 在 `fixed-linux-eval` 上执行、官方口径评分、结果与对象可查询。

## 验收

- UI 与 `prototype/` 原型对齐，差异清单在 PR 中记录并逐条处置。
- 真实用例 attempt 产生 score 与 trace 对象、终态落 PostgreSQL，结果 API 可查询。
- 重复触发同一评测生成独立 attempt，旧结果与轨迹不被覆盖。
- 一致性检查阻塞项在启动向导中可见并可解释。
- 真实 OSS 上传成功且 HEAD 校验通过；冒烟报告（computer-use-smoke-reporting 体例）产出并如实标注 GUI 冒烟替代方式。

# 修复固定资源池标签被误解析导致评测分片永久排队

[English](m7-fixed-pool-runner-labels.en.md) | 中文

```yaml flow
kind: issue
milestone: m7
priority: P1
status: done
scope:
  - plans/**
  - reports/**
  - runner/**
  - tests/**
  - .agents/flow/milestones/m7-extension-and-hardening.*
  - backend/app/services/runner_labels.py
  - backend/app/services/runners.py
  - tests/backend/test_runner_labels.py
  - tests/backend/test_evaluations.py
  - tests/backend/test_runners.py
  - .agents/flow/issues/m7-fixed-pool-runner-labels.*
adr: []
github:
  number: 136
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/136
```

## 背景

2026-11-11 15:25（北京时间）用户在 fixed-linux 系统资源池发起评测 707bb3bc，execute 作业一直排队。排查发现系统固定池的 labels_json 被写成 `{"kind": "fixed"}`，而分片派发把 labels_json 的键当作 Runner 标签，生成的 runs_on 为 `["kind"]`；实际 Runner 只注册 self-hosted/linux/eval，作业永远等不到机器。该问题影响所有选择固定系统池的评测，队列超时后整次评测失败。

## 目标

- 将 `{"kind": "fixed"}` 识别为历史 provider 标记而非 Runner 标签；未显式配置 runs_on/labels 的固定池回退到 self-hosted、linux、eval。
- ensure_fixed_pool 新建系统池时写入显式 runs_on，避免继续产生占位标记。
- 补充分片 manifest/dispatch 与固定池创建的回归测试，防止再次回归。

## 验收

- labels_json 为 `{"kind": "fixed"}` 的固定池派发出 runs_on `["self-hosted", "linux", "eval"]`。
- 固定池显式配置 runs_on 或 labels 时仍优先生效。
- ensure_fixed_pool 新建的池 labels_json 含显式 runs_on。
- 部署后新建评测能落到实际在线的固定 Runner；已超时的 707bb3bc 需重新发起。
- 相关 pytest、flow 与文档门禁通过。

## 验收结果

- 生产 fixed-linux 池仍保留历史 `{"kind": "fixed"}`，部署后的解析结果为 self-hosted、linux、eval。
- 2026-11-12 新评测 `64e90bd2-e715-4c34-94ff-489782fc9041` 已由 fixed-linux-eval 接收并进入运行中；对应 eval-shard run 为 43121931498。
- 这次真实执行暴露了容器退出后 Claude 原生文件权限不可读的独立问题；本分支在删除容器前由 root 统一恢复 `/testbed` 可读写遍历权限，并补权限回归。
- 权限修复部署后的复测又暴露 heartbeat 与 terminal callback 的 sequence 冲突；修复后用新评测 `a04b8881-1513-4514-a9f5-b98d2225d6ad` 验证 execute job 成功、结果 resolved、轨迹 ready。
- 原 707bb3bc 保持历史失败记录，不作为可恢复任务改写。

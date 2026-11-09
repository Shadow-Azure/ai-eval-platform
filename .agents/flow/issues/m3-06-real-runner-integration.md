# 评测 Shard 接入真实 Claude Code Runtime 与官方 SWE-bench 评分

[English](m3-06-real-runner-integration.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: ready
scope:
  - .agents/flow/issues/m3-06-real-runner-integration.*
  - .agents/flow/issues/m3-06-admin-operations-status.*
  - runner/**
  - .github/workflows/eval-shard.yml
  - backend/**
  - tests/**
  - reports/**
adr: []
github:
  number: 63
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/63
```

## 背景

M2-05 用 synthetic-task 打通了 dispatch/token/upload/callback；M3-04 的真实 E2E 是手工在临时环境跑 Claude Code + 官方 harness 后上传报告，生产 `eval-shard` 仍对所有任务调用 synthetic-task。生产评测 `a80b3802` 因此是 succeeded/not_scored，Claude Code 与官方 SWE-bench 评分尚未进入正常 runner 路径。

## 目标

- `eval-shard` 对 dataset 任务选择 real runtime，按 `task_json` 执行 Claude Code，注入短期模型 token，采集 patch/trace。
- 在隔离执行镜像内应用 test patch，运行官方 F2P/P2P，生成按 `instance_id` 组织的官方 report。
- 通过现有 presigned 上传 score/trace，completion 后 results API 显示 resolved/not_scored 与测试级明细。
- 保留 synthetic 路径与 M2 回归；模型 key、长期凭据和私有内容不进入日志或对象。

## 验收

- 生产 UI 发起 astropy__astropy-12907 真实评测，经 `fixed-linux-eval` 自动执行并 resolved；results API 返回 100%、2 F2P + 13 P2P，无需手工上传报告。
- synthetic 评测仍 resolved，runner-contract 与 M2 回归测试全绿。
- 失败、超时、空补丁分别落 not_scored/unresolved，错误可诊断。
- 日志与对象不含模型 key，短期 token 绑定 eval_run/shard/attempt。
- backend 与 runner CI 全绿，报告和截图归档到 `reports/`。

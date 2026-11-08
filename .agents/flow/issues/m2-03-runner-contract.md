# 定义通用 Runner 任务协议与合成任务工作流

[English](m2-03-runner-contract.en.md) | 中文

```yaml flow
kind: issue
milestone: m2
priority: P0
status: done
scope:
  - .agents/flow/issues/m2-03-runner-contract.*
  - runner-contract/**
  - runner/**
  - tests/runner/**
  - tests/fixtures/runner/**
  - docs/architecture.*
  - plans/**
adr: []
github:
  number: 13
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/13
```

## 背景

评测执行必须与具体数据集、Agent 和云资源解耦。M2 先冻结可重放的任务引用、清单和回传协议，并用不调用模型的合成任务验证协议，真实评测能力在 M3 接入。

## 目标

- 定义携带 eval_run、shard、attempt、manifest 地址与 digest 的最小派发契约。
- 定义任务清单、阶段心跳、上传清单、完成与失败回传的版本化 Schema。
- 提供私有 Runner 仓库可复用的通用 workflow 模板和合成任务执行脚本。
- 为清单校验、重复派发、对象 digest、成功回传与可重试失败建立契约测试。

## 验收

- 相同 dispatch key 重复到达时不会启动第二个有效执行。
- 清单地址、digest、attempt 或 token 任一不匹配时，任务在执行前明确拒绝。
- 合成任务可获取清单、生成确定性结果、上传对象并提交结构化完成回调。
- 协议在本地 fixture 和真实 GitHub workflow 中使用同一份 Schema，不包含数据集或 Agent 特例。

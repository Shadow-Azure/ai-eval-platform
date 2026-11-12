# [m8-11] 评测时跳过 codespec init，改用冻结快照注入

[English](m8-11-eval-skip-init-snapshot-injection.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-11-eval-skip-init-snapshot-injection.*
  - .agents/flow/issues/m8-10-cli-generated-agent-assets.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - runner/scripts/real-task.mjs
  - tests/runner/**
  - plans/**
adr: []
github:
  number: 204
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/204
```

## 背景

- 生产验证 m8-10 时，评测 Runner 在容器内执行 install + init；init 以 `eval` 用户创建 `.claude`，随后宿主机把冻结快照物化到同一 `.claude` 时因属主不同报 `EACCES`（`.claude/agents`），agent 未启动、评测失败。
- 决策：评测时不再执行 init，只执行 install（保证 CLI 可用），`.claude` 完全由冻结快照注入；init 仍只在资产源加载测试中使用，用于生成资产与项目状态。
- 这样既避免属主冲突，也保持“评测使用冻结快照”的可复现语义。

## 目标

- Runner 评测阶段只执行 `install_command`；即使 payload 带有 `init_command` 也不执行。
- 冻结快照的 `.claude` 资产照常物化，属主 chown 回 `eval`；不再有 init 产物与快照争抢。
- install 失败以 `asset_runtime_setup_failed` 中止；命令为空时保持原有行为（无 setup）。
- 更新 runner 测试：install 执行、init 不执行、快照覆盖、失败中止。
- 说明：评测工作区不再由 init 预创建 `openspec/` 项目状态；需要时由 agent 或命令自行初始化。

## 验收

- 评测容器内不再出现 `.claude` 属主/覆盖冲突；`.claude` 来自冻结快照。
- trace 中可见 CLI 与快照命令/技能可用，agent 能按 prompt 执行命令流程。
- runner 测试全部通过；`npm test` 与 CI 通过。
- 部署后生产重跑评测，确认 design→propose→apply 可执行。

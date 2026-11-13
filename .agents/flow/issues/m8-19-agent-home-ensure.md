# [m8-19] runner 在资产 install 前确保 /home/eval 存在

[English](m8-19-agent-home-ensure.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-19-agent-home-ensure.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - runner/scripts/real-task.mjs
  - .github/workflows/backend-ci.yml
  - tests/runner/**
  - plans/**
adr: []
github:
  number: 219
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/219
```

## 背景

- 代理（3128/3129）已经修好并在监听，但两次生产评测都失败在 runner 的前置安装：npm 报 `ENOENT: no such file or directory, mkdir '/home/eval/lib'`——资产 install 以 eval 用户执行时 `/home/eval` 不存在。
- 同一镜像在 m8-15 的评测里曾通过，说明是容器初始化/顺序不稳定；当前只依赖适配器 installScript 末尾的 `mkdir -p /home/eval`，没有兜底也没有诊断。

## 目标

- runner 在资产 install 之前，以 root 幂等执行 `mkdir -p /home/eval && chown eval:eval /home/eval`，并输出 `id` 与 `ls -ld /home /home/eval` 诊断。
- 该步骤失败时给出明确日志（不静默）。

## 验收

- 评测的资产 install 不再出现 `/home/eval` ENOENT。
- 经 3129 代理的安装与 design→propose→apply 可继续验证。

# [m8-20] 资产 install/agent 使用可写的 /tmp/eval-home

[English](m8-20-eval-home-writable.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-20-eval-home-writable.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - runner/scripts/real-task.mjs
  - tests/runner/**
  - plans/**
adr: []
github:
  number: 221
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/221
```

## 背景

- m8-19 已让 runner 在资产 install 前以 root 创建 `/home/eval`，但评测仍报 `ENOENT: mkdir '/home/eval/lib'`：该镜像里 `/home/eval` 对 eval 用户不可见/不稳定，单靠 mkdir 解决不了。

## 目标

- 资产 install 改用保证可写的 `HOME=/tmp/eval-home`，CLI 安装进 `$HOME/bin`。
- agent 的 PATH 增加 `/tmp/eval-home/bin`，保证 `.claude/commands/opsx/*` 能调用到 `codespec`。
- install 前输出 `id` 与 `ls -ld /home /home/eval /tmp/eval-home`，失败时日志可定位。

## 验收

- 资产 install 不再出现 `/home/eval` ENOENT；CLI 可被 agent 调用。
- 经 3129 代理的 GitHub 下载与 design→propose→apply 可继续验证。

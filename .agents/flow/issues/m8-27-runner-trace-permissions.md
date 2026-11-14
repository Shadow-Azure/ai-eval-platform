# [m8-27] 修复 Runner trace 目录权限，保留 Agent 失败诊断

[English](m8-27-runner-trace-permissions.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-27-runner-trace-permissions.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - runner/scripts/real-task.mjs
  - tests/runner/trace-permissions.test.mjs
adr: []
github:
  number: 238
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/238
```

## 背景

`real-task.mjs` 先对 workdir 执行 `chmod -R a+rwX`，之后才创建 `workdir/trace`；trace 目录 mode 受 Runner 进程 umask 影响（0022 → 0755），且属主是 Runner 用户。Agent 以 `eval` 用户运行，失败后 Runner 写 `trace/diagnostics` 会 EACCES，把真实 agent stderr 吞掉。OpenSpec source-mode 的 34s 失败因此只能看到 `agent exit 1`。

## 目标

- 在递归 chmod 之前创建 `.runtime-home` 和 `trace`，或显式 chmod 0777；不依赖 umask/属主。
- 回归测试锁定创建顺序，确保非 root eval 用户可写 trace。
- 部署后重跑 OpenSpec source-mode，拿到真实 agent stderr。

## 验收

- `mkdirSync(trace)` 在 `chmod -R a+rwX workdir` 之前；测试覆盖。
- 失败诊断不再因 trace 目录权限丢失 agent stderr。
- Runner CI 通过；生产重跑能输出真实 Claude 错误。

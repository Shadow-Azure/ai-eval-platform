# [m8-29] source CLI 通用 linker 改为可执行 wrapper

[English](m8-29-source-bin-wrapper.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-29-source-bin-wrapper.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - runner/scripts/link-source-bins.mjs
  - tests/runner/agent-source-path.test.mjs
adr: []
github:
  number: 242
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/242
```

## 背景

PR #241 的 linker 用 symlink 指向 `codespec.js`，但源文件是 0644，执行报 Permission denied；临时 install wrapper 被 linker 覆盖。

## 目标

- linker 为每个 `bin` 写 0755 wrapper（`#!/bin/sh exec <node> <source> "$@"`），不依赖源文件可执行位。
- 测试覆盖 0644 源文件仍能执行；部署后重跑 OpenSpec。

## 验收

- `codespec` 可执行；Runner 测试/CI 通过；生产 trace 出现 design/propose/apply。

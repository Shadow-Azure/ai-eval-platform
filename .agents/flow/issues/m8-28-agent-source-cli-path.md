# [m8-28] Agent PATH 包含 source CLI，确保 /opsx 命令可执行

[English](m8-28-agent-source-cli-path.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-28-agent-source-cli-path.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - runner/adapters/runtimes/claude-code.mjs
  - runner/scripts/link-source-bins.mjs
  - tests/runner/agent-source-path.test.mjs
adr: []
github:
  number: 240
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/240
```

## 背景

trace 权限修复后，OpenSpec source-mode 的 `/opsx:design` 已能展开，但 design skill 调 `codespec` CLI 时返回 `command not found`；Agent 只能自行改代码，design→propose→apply 未真正执行。

## 根因

`claude-code` 的 `agentScript()` PATH 只有 conda testbed 和 `/opt/node/bin`；source-mode 的 `/asset-source/bin`、`/asset-source/node_modules/.bin`、`/home/eval/bin` 等只在 install/init 的 PATH 中。且 CLI 文件名是 `codespec.js`，没有裸 `codespec` 命令。

## 目标

- Agent PATH 加入 source CLI 目录。
- 新增通用 `link-source-bins.mjs`：读取 source package `package.json` 的 `bin` 映射，把所有声明命令链接到 `/home/eval/bin`，不硬编码 `codespec`。
- 测试锁定 PATH/链接；部署后重跑 OpenSpec，确认 design→propose→apply 真正执行。

## 验收

- `agentScript()` PATH 包含 source CLI 目录；通用 bin 链接器覆盖任意 source CLI。
- Runner CI 通过；生产 trace 出现 codespec/design 阶段，而非 command not found。

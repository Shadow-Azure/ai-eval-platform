# [m8-21] 全源码模式：评测按 ref clone + 扫描/构建，前端支持默认分支与版本选择

[English](m8-21-source-mode-ref-selection.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: backlog
scope:
  - .agents/flow/issues/m8-21-source-mode-ref-selection.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - prototype/**
  - backend/app/**
  - backend/migrations/versions/**
  - runner/**
  - web/**
  - tests/**
  - docs/**
  - plans/**
adr: []
github:
  number: null
  url: null
```

## 背景

- 当前评测使用“加载测试冻结的资产快照 + 预构建 tarball”：快、稳，但换分支/Tag/Commit 就要重新打包，无法在启动评测时对同一个 Agent 资产换版本测试。
- 无命令类资产（SuperPower，`obra/superpowers`）的真实结构是 `skills/`、`.agents/`、`.claude-plugin/`、`.opencode/`、`.pi/` 等原生目录，由平台加载器扫描并归一化成 `.claude`，仓库里并没有 `.claude`。

## 目标

- 全源码模式：评测一律按选定 ref clone 资产仓库；无命令类由平台扫描/归一化仓库原生资产，有命令类执行 install/init 生成 `.claude`；冻结快照只保留为加载测试的校验记录。
- 资产源配置“默认分支”；评测准备页可选“Agent 版本”（分支/Tag/Commit），后端解析为 commit 并写进 payload；trace 记录 repo/ref/commit。

## 方案

### 两类资产
| 类型 | 仓库内容 | 评测时 |
| --- | --- | --- |
| 无命令类（SuperPower） | `skills/`、`.agents/`、`.opencode/`、`.pi/` 等 | clone+checkout → 平台扫描/归一化 → `.claude` → 注入；不跑 install/init |
| CLI 生成类（CodeSpec） | 源码 + install/init | clone+checkout → install/build → init 生成 `.claude` → 注入 |

### 流程
准备页选 ref → 后端解析 commit → payload → Runner clone/checkout → 扫描或 install/init → 注入 → AI 执行。

### 前端原型（修改前 / 修改后）

修改前（资产源显示“默认 Ref”、评测页显示“评测 Ref”）：

![修改前-资产源](https://raw.githubusercontent.com/Shadow-Azure/ai-eval-platform/agent/m8-21/superpower-agent-eval-41d71917e720/prototype/screenshots/m8-21-before-assets.png)
![修改前-评测 Agent 版本](https://raw.githubusercontent.com/Shadow-Azure/ai-eval-platform/agent/m8-21/superpower-agent-eval-41d71917e720/prototype/screenshots/m8-21-before-eval.png)

修改后（资产源显示“默认分支”、评测页显示“Agent 版本（分支 / Tag / Commit）”，并说明会按该版本 clone + 扫描/构建）：

![修改后-资产源](https://raw.githubusercontent.com/Shadow-Azure/ai-eval-platform/agent/m8-21/superpower-agent-eval-41d71917e720/prototype/screenshots/m8-21-after-assets.png)
![修改后-评测 Agent 版本](https://raw.githubusercontent.com/Shadow-Azure/ai-eval-platform/agent/m8-21/superpower-agent-eval-41d71917e720/prototype/screenshots/m8-21-after-eval.png)

## 分期

1. 资产加载器支持按 ref 扫描（无命令类）；
2. 准备接口 + 准备页 ref 覆盖；
3. Runner 全源码模式（关闭冻结快照注入）；
4. 前端/文档/测试完善，移除旧注入路径。

## 验收

- 同一 Agent 资产在评测启动时可切换分支/Tag/Commit，并按该版本 clone/扫描/构建。
- trace 记录 repo/ref/commit；两类资产都能正常注入并执行。
- 前端原型修改前后截图见上。

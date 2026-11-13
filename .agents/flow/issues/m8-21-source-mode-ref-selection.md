# [m8-21] CLI 生成类改成评测时 checkout ref + install/init

[English](m8-21-source-mode-ref-selection.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
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
  number: 223
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/223
```

## 背景

- 评测页已有 assetRef（分支/Tag/Commit），prepare 会按 ref 解析并扫描；但 CLI 生成类的 `init` 仍只在加载测试（默认分支）执行，产物冻结进快照，评测注入的还是默认分支那份 `.claude`。
- 无命令类（Superpower）走“仓库原生目录扫描 + 冻结”的路径，改 ref 已生效，本 issue 不改它。

## 目标

- 只改 CLI 生成类：评测时按所选 ref `git clone` + `checkout commit`，执行资产源配置的 install（源码构建）与 init（生成 `.claude`），再交给 AI；该类不再注入冻结快照。
- 无命令类保持现状（加载测试扫描 + 冻结快照）。
- trace 记录 repo/ref/commit、install/init 输出与 `.claude` 清单。

## 方案

### 流程
评测页选 ref → 后端解析 commit → payload 带 repo_url/commit + install/init → Runner clone/checkout → install（源码构建）→ init 生成 `.claude` → AI 执行。

### 改动
| 层 | 改动 |
| --- | --- |
| 资产源 | CLI 生成类的 install_command 由“装预构建 tarball”改为“源码构建”（如 npm ci && npm run build）；init_command 保持 codespec init |
| 后端 | payload 增加 repo_url + resolved_commit；该类标记 source mode；无命令类不变 |
| Runner | 该类 clone/checkout/install/init，跳过冻结注入；HOME 用 /tmp/eval-home（m8-20） |
| 前端 | 沿用 assetRef，补“将按该版本 checkout 并重新构建/init”文案（原型已体现） |
| trace | 记录 repo/ref/commit 与 install/init 输出 |

## 关键取舍

- init 回到评测时执行（仅 CLI 生成类），推翻 m8-11 的“评测不 init”；init 在容器内以 eval 在 /testbed 执行，属主一致。
- 每次评测多一次 clone+install+build+init；浅克隆、依赖缓存可缓解。
- 私有仓库用平台 Git 凭据，经代理注入、不落 trace。

## 验收

- 同一 CLI 生成类资产在评测启动时切换分支/Tag/Commit，按该版本 checkout、构建、init，并用该版本的 `.claude` 执行。
- 无命令类行为不变；trace 含 repo/ref/commit 与构建日志。

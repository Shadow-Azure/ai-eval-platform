# [m8-22] CLI 生成类：评测时按 ref checkout + install/init

[English](m8-22-cli-source-checkout.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-22-cli-source-checkout.*
  - .agents/flow/issues/m8-21-source-mode-ref-selection.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - backend/app/**
  - backend/migrations/versions/**
  - runner/**
  - web/**
  - prototype/**
  - tests/**
  - docs/**
  - plans/**
adr: []
github:
  number: 225
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/225
```

## 问题

- 评测页已有 assetRef（分支/Tag/Commit），prepare 会按 ref 解析并扫描；对无命令类资产（Superpower）改 ref 已能生效。
- 但 CLI 生成类（CodeSpec）的 `init` 仍只在加载测试（默认分支）执行，产物冻结进快照；评测时 Runner 只注入这份冻结 `.claude` 并执行运行时 install。结果是：改分支后 install 用了新 ref，但 `.claude` 还是默认分支那份，分支切换对流程不生效。

## 方案

只改 CLI 生成类：评测时按所选 ref 获取源码并 checkout 到解析后的 commit，执行资产源配置的 install（源码构建）与 init（生成 `.claude`），再交给 AI；该类不再注入冻结快照。无命令类保持现状。

平台在解析资产时按 commit 生成源码归档并存入对象存储，评测时下发短期预签名 URL；Runner 解包后得到与 `git clone` + `git checkout <commit>` 等价的源码树。这样 Runner 主机不接收平台长期 Git 凭据，私有仓库仍由平台侧凭据读取。

### 流程
评测页选 ref → 后端解析 commit → 解析资产时冻结源码归档 → 评测 payload 带 repo_url/commit + install/init + 短期源码 URL → Runner 下载解包 → install（源码构建）→ init 生成 `.claude` → 注入仓库内选中的静态资产 → AI 执行。

### 改动
| 层 | 改动 |
| --- | --- |
| 资产源 | CLI 生成类的 install_command 由“装预构建 tarball”改为“源码构建”（如 `npm ci && npm run build && npm install -g .`）；init_command 仍为 `codespec init` 一类命令；加载测试也在按 ref 取到的源码树中执行 |
| 后端 | 快照冻结 `source_mode`、`repo_url`、`source_archive_*`；payload 增加 `source_mode` / `repo_url` / 短期 `source_archive_url`；无命令类不变 |
| Runner | CLI 生成类解包源码、执行 install/init、注入选中静态资产、校验选中资产已生成，跳过冻结 `.claude`；HOME 保持 `/home/eval`（m8-19） |
| 前端 | 沿用 assetRef，补“按该版本 checkout 源码并重新构建/init”文案；资产源默认分支与加载测试说明同步 |
| trace | 记录 repo/ref/commit、install/init 输出与静态资产清单（不含凭据） |

## 前端原型

修改前（“默认 Ref”“评测 Ref”）与修改后（“默认分支”“Agent 版本（分支 / Tag / Commit）”）的截图见 PR #224 与 issue #223；本 issue 在 Web 实现同一文案，并以 `prototype/` 为 UI 基线。

## 关键取舍

- init 回到评测时执行（仅 CLI 生成类），推翻 m8-11 的“评测不 init”；init 在容器内以 eval 在 /testbed 执行，属主一致。
- 平台在解析时多下载、存储一份 commit 源码归档；评测时多一次解包 + install/build + init。用 commit 去重归档，避免同一版本重复存储。
- 不用 Runner clone：Runner 机器不存长期凭据（架构约束），平台侧取源码后只下发短期 URL；私有仓库凭据不进入 trace。
- 源码归档上限 128 MiB；超限时解析直接失败并给出明确错误，而不是静默回退到默认分支快照。

## 验收

- 同一 CLI 生成类资产在评测启动时切换分支/Tag/Commit，按该版本 checkout、构建、init，并用该版本的 `.claude` 执行。
- 无命令类行为不变；trace 含 repo/ref/commit 与构建日志。
- 加载测试同样在源码树中执行；私有仓库加载测试复用扫描/创建时的令牌。

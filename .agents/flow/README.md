# 开发流程门禁

[English](README.en.md) | 中文

> 本目录只定义 roadmap → milestone → issue 的工作契约。当前尚未创建 roadmap、milestone 或 issue；用户完成规划后，下列门禁自动启用。

## 层级

| 层 | 文件 | 职责 |
|---|---|---|
| roadmap | `roadmap.md` | 愿景与 milestone 有序序列的唯一事实源 |
| milestone | `milestones/<id>-*.md` | 阶段目标与验收 |
| issue | `issues/<milestone-id>-*.md` | 最小工作单元、优先级与允许改动的 scope |

## 机读围栏

每份工作文件必须恰好包含一个 `yaml flow` 围栏。中英文配对文件的围栏必须逐字节一致，避免机读字段漂移。

roadmap 字段：`kind: roadmap`、`milestones`。

milestone 字段：`kind: milestone`、`id`、`title`、`status`、`keep-open`（可选）、`github: {number, url}`。

issue 字段：`kind: issue`、`milestone`、`priority`、`status`、`scope`、`adr`、`github: {number, url}`。流程自举时可在首个 milestone 内使用一次 `bootstrap: true`，编号回填后移除。

## 封闭集合

- issue status：`backlog` / `ready` / `in-progress` / `done`
- milestone status：`planned` / `active` / `done`
- priority：`P0` / `P1` / `P2`

## 推进顺序

- issue 编号与 GitHub issue 编号一致；提交与 PR 标题必须包含 `(#N)`。
- `github.number` 为 `null` 的 issue 只能处于 `backlog`，同步回填编号后才能流转。
- 跨 milestone 的同优先级串行；同 milestone 同优先级允许并行。
- milestone 为 `done` 当且仅当其 issue 全部为 `done`；标了 `keep-open: true` 的常驻里程碑（如持续接收同类问题的缺陷修复里程碑）可在 issue 全部 done 后保持 `active`。

## 门禁

| 检查 | 位置 | 拦截内容 |
|---|---|---|
| `npm test` | 本地 + CI | 门禁自身回归 |
| `npm run verify:flow` | 本地 + CI | 文件格式、状态机、优先级、scope、ADR 链接 |
| `verify-flow --diff origin/main` | CI PR | 提交/PR 标题引用、issue 状态与改动 scope |
| `npm run verify:flow-online` | CI PR | GitHub issue/milestone 状态、标题、URL、PR milestone 与 Project 关联 |
| `commit-msg` | 本地 | 提交标题缺 `(#N)` 时提醒并阻断 |

在 roadmap 尚未创建时，静态和在线 CI 会明确记录“pending”并跳过；roadmap 首次合入后自动启用，无需再改工作流。

在线 Project 检查强制开启，要求仓库 Secret `FLOW_TOKEN` 具备读取用户级 Projects v2 的权限。本地运行需先完成 `gh auth login`。

## 工具

初始化钩子、生成 issue 三件套并把本地流程树同步到 GitHub：

```sh
npm run install:hooks
npm run flow:new-issue -- --milestone <id> --priority P0 --slug <kebab> \
  --title <Chinese title> --title-en "<English title>" --scope "<glob,glob>"
npm run flow:sync
```

# [m10] 评测模板停用后展示删除按钮与确认浮窗

[English](m10-template-delete-confirm.en.md) | 中文

```yaml flow
kind: issue
milestone: m10
priority: P2
status: ready
scope:
  - prototype/**
  - .agents/flow/issues/m10-template-delete-confirm.md
  - .agents/flow/issues/m10-template-delete-confirm.en.md
  - .agents/flow/issues/m10-template-delete-confirm.i18n.yaml
adr: []
github:
  number: 416
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/416
```

## 背景

2026-11-23 反馈：生产「评测模板」列表中，停用模板的操作区仍保留一个禁用的「启动」按钮。用户希望停用后把「启动」替换为「删除」，点击弹出确认浮窗，确认后删除模板。

停用模板已经不能启动，把这个操作位改成删除更符合当前可执行的动作；删除只能从停用状态发起，避免在启用模板上误删。

## 现状与缺口

| 位置 | 现状 | 缺口 |
|---|---|---|
| 生产评测模板列表 | 停用行展示「已停用」状态、启用与禁用的「启动」按钮 | 没有删除入口，禁用启动按钮占据操作位 |
| 原型评测模板列表 | 停用行展示「已停用」状态与禁用的「启动」按钮 | 没有删除按钮与确认浮窗，操作位仍是禁用启动 |
| 原型模板数据 | 有停用模板 `tpl-010`、`tpl-017` 与状态筛选 | 没有删除动作，也没有删除后的列表状态 |

## 目标

- 评测模板列表：停用模板的「启动」按钮替换为红色「删除」按钮；启用模板保持「启动」。
- 点击「删除」弹出确认浮窗，说明删除不可恢复、历史评测仍保留；「取消」关闭浮窗且不删除。
- 确认删除后从模板列表移除该模板并提示；删除最后一个匹配项时展示空态。
- 原型先落地并附修改前后截图，生产实现按本 issue 跟进。

## 验收

- 停用行操作区显示「详情 / 接口 / 历史 / 删除」，不再出现禁用启动。
- 点击「删除」出现确认浮窗；取消后模板仍在，确认后行消失、计数减少并出现提示。
- 删除只影响模板本身，已启动的评测记录与轨迹保留。
- 修改前后截图使用同一视口、同一主题、同一数据，附在本 issue 与原型 PR。
- `npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与 PR CI 通过。

## 非目标

- 本 issue 的原型 PR 不修改生产 `web/` 与 `backend/`，不新增删除接口。
- 不增加批量删除、停用/启用切换或删除启用中的模板。
- 不改变模板启动、评测执行、调度、评分与轨迹链路。

## 参考

- 相关：issue #378（评测模板）、issue #394（模板启动历史）、issue #410（模板来源）。
- 设计：[docs/design/eval-platform-architecture.md](../../../docs/design/eval-platform-architecture.md) §5.14。

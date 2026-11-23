# [m10] 评测任务记录展示来源模板

[English](m10-template-origin.en.md) | 中文

```yaml flow
kind: issue
milestone: m10
priority: P0
status: done
scope:
  - prototype/**
  - web/src/pages/EvalsPage.tsx
  - web/src/api/evaluations.ts
  - web/src/components/TemplateBits.tsx
  - web/src/styles/overview.css
  - backend/app/services/evaluations.py
  - backend/app/services/eval_templates.py
  - backend/app/api/evaluations.py
  - tests/backend/test_evaluations.py
  - tests/backend/test_eval_templates.py
  - docs/design/eval-platform-architecture.md
  - docs/design/eval-platform-architecture.en.md
  - docs/design/eval-platform-architecture.i18n.yaml
  - scripts/doc-budgets.manifest.json
  - plans/**
  - .agents/flow/issues/m10-template-origin.md
  - .agents/flow/issues/m10-template-origin.en.md
  - .agents/flow/issues/m10-template-origin.i18n.yaml
adr: []
github:
  number: 410
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/410
```

## 背景

生产评测任务列表（2026-11-23 反馈）里，由评测模板一键启动的评测与自定义发起的评测在记录上没有可区分的来源：行内只有任务名、运行 ID 与资源池。评测详情已有“由评测模板「…」启动”的横幅，但列表无法看出某条记录来自哪个模板，也无法从记录跳到模板。用户希望在评测任务记录里直接看到“来自评测模板”。

## 现状与缺口

| 位置 | 现状 | 缺口 |
|---|---|---|
| 生产评测任务列表 | 行内显示任务名、运行 ID、资源池 | 不显示来源模板，无法区分模板启动与自定义发起 |
| 原型评测任务列表 | 行内显示任务名、运行 ID、资源池 | 与生产一致，同样缺少来源 |
| 生产评测详情 | 详情接口返回 `template`，页头展示来源模板横幅 | 列表没有来源，用户仍不知道记录从模板启动 |
| 原型评测详情 | 仅当 URL 带 `?fromTemplate=` 时展示横幅 | 从列表直接进入时不展示，来源依赖跳转参数而不是运行记录 |
| 评测列表 API | 摘要返回 `template_id`、`template_version` | 不返回模板名称，列表无法直接展示可读来源 |

## 目标

- 评测任务列表：模板启动的行显示“来自模板 · 模板名”，模板名可进入模板详情；自定义发起的行保持原样。
- 评测详情：来源模板从运行记录的 `template_id` 解析，不再依赖 `?fromTemplate=`；模板启动显示横幅，自定义评测不显示。
- 不新增执行接口；列表摘要已有 `template_id` 与 `template_version`，生产实现可补充模板名称或复用模板读接口。
- 原型先落地并附修改前后截图，生产实现按本 issue 跟进。

## 验收

- 列表模板启动行显示来源标签与模板名；点击模板名进入对应模板详情，点击行其他区域仍进入评测详情。
- 列表自定义发起行不显示来源行。
- 由列表进入模板启动的评测详情时显示“由评测模板「…」启动”横幅；自定义评测不显示。
- 模板名超长时截断并保留完整 title；表格列宽与行高不破坏现有布局。
- 修改前后截图使用同一视口、同一主题、同一数据，附在本 issue 与原型 PR。
- `npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与 PR CI 通过。

## 非目标

- 本 issue 的原型 PR 不修改生产 `web/` 与 `backend/`；生产实现按验收项后续跟进。
- 不在列表增加按模板筛选、分组统计或模板版本列。
- 不改变评测执行、调度、评分与轨迹链路。

## 参考

- 相关：issue #378（评测模板）、issue #394（模板启动历史）。
- 设计：[docs/design/eval-platform-architecture.md](../../../docs/design/eval-platform-architecture.md) §5.14。

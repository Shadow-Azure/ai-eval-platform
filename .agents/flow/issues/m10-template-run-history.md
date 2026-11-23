# [m10] 评测模板新增启动历史浮窗

[English](m10-template-run-history.en.md) | 中文

```yaml flow
kind: issue
milestone: m10
priority: P2
status: ready
scope:
  - prototype/**
  - plans/**
  - .gitignore
  - backend/app/api/evaluations.py
  - backend/app/services/evaluations.py
  - tests/backend/test_eval_templates.py
  - web/src/api/evaluations.ts
  - web/src/components/TemplateHistoryModal.tsx
  - web/src/pages/EvalTemplatesPage.tsx
  - web/src/pages/EvalTemplateDetailPage.tsx
  - web/src/styles/templates.css
  - docs/design/eval-platform-architecture.md
  - docs/design/eval-platform-architecture.en.md
  - docs/design/eval-platform-architecture.i18n.yaml
  - scripts/doc-budgets.manifest.json
  - .agents/flow/issues/m10-template-run-history.md
  - .agents/flow/issues/m10-template-run-history.en.md
  - .agents/flow/issues/m10-template-run-history.i18n.yaml
adr: []
github:
  number: 394
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/394
```

## 背景

生产「评测模板」列表每行只有详情、接口、停用与启动，模板详情页只内嵌最近 5 次运行；模板累计启动多次后，用户无法在模板上下文查看更早的评测，也无法从模板回到某一次历史评测。评测列表接口已支持状态、数据集与分页，但缺少按模板过滤的读取契约。

## 目标

- 模板列表行新增「历史」，模板详情顶部与「最近运行」卡头新增「启动历史 / 全部历史」，共用同一个浮窗。
- 浮窗按服务端分页读取该模板的历史评测，默认每页 10 条并支持 10/20/50 切换，支持按状态筛选并显示计数（0 也保留）。
- 任意历史行或「查看」进入对应评测详情，空态与筛选无结果有明确说明和清除操作。
- 复用 `GET /api/v1/evaluations` 并增加可选 `template_id` 过滤，不新增执行接口；详情页 `recent_runs` 仍只保留最近 5 条预览。

## 验收

- 原型列表行与详情页入口可见，浮窗默认 10 条/页，翻页、状态筛选与计数正确，筛选后回到第 1 页。
- 历史行进入对应评测详情，不再落到默认评测；0 次启动与筛选无结果均显示可用空态。
- 修改前后截图使用同一视口与主题，并附在 issue #394 与原型 PR。
- 设计文档 §5.14 记录 `template_id` 过滤、分页语义与只读边界。
- `npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与 PR CI 通过。

## 参考

- issue：https://github.com/Shadow-Azure/ai-eval-platform/issues/394
- 设计：[docs/design/eval-platform-architecture.md](../../../docs/design/eval-platform-architecture.md)

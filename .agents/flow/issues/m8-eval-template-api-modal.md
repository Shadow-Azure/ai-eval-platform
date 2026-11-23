# [m10] 评测模板「模板接口」弹窗与原型不一致

[English](m8-eval-template-api-modal.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - web/src/components/TemplateApiModal.tsx
  - web/src/pages/EvalTemplateDetailPage.tsx
  - web/src/pages/EvalTemplatesPage.tsx
  - web/src/styles/templates.css
  - plans/**
  - .agents/flow/issues/m8-eval-template-api-modal.md
  - .agents/flow/issues/m8-eval-template-api-modal.en.md
  - .agents/flow/issues/m8-eval-template-api-modal.i18n.yaml
adr: []
github:
  number: 391
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/391
```

## 背景

生产「模板接口」弹窗与 `prototype` 存在六处漂移：弹窗高 952px、超出 837px 视口且没有页脚；长 curl 行把代码块和整项撑到 834px，越过 760px 弹窗；摘要为单列三行；GET/POST 方法标签是灰/蓝而非蓝/绿；副标题重复鉴权说明。根因是 M10 #378 在 React 中新增 `TemplateApiModal` 时没有迁移民型的 `openTemplateApi`、`.modal.wide` 与 `.api-*` 约束。

## 目标

- 弹窗高度限制为 `calc(100vh - 48px)`，正文内部滚动，标题与页脚固定可见。
- 补页脚「关闭 / 一键启动」，启动入口由详情页和列表页关闭接口弹窗后打开启动弹窗。
- 代码块 `white-space: pre-wrap; overflow-wrap: anywhere`，并给网格项与路径行 `min-width: 0`，长 curl 不再越过弹窗。
- 摘要恢复原型三列，窄视口回退单列；方法标签按原型 GET 蓝 / POST 绿。
- 副标题只保留模板名与三个执行接口，鉴权只在摘要行出现。
- 1500×837 与 1200×837 下用同视口、同主题的前后截图和 DOM 断言验证，截图不提交进代码仓。

## 验收

- 1500×837 下弹窗完整落在视口内（约 760×789，top=24），正文 `scrollWidth <= clientWidth`，页脚可见。
- 1200×837 下弹窗无水平溢出，摘要仍为三列，代码块在弹窗内换行。
- 方法标签 GET 蓝、POST 绿；副标题不再包含鉴权说明；摘要为三列。
- 页脚「关闭」可关闭弹窗，「一键启动」打开启动确认弹窗。
- 原型在相同数据与视口下呈现同一结构；修改前后截图同视口、同主题并附在实现 PR。
- `npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与 `web` 构建通过。

## 参考

- 实现 PR：#393（检视记录见 `plans/2026-11-23-pr393-review.md`）。

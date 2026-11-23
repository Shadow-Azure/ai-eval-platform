# [m10] 评测模板列表长名称下布局塌陷

[English](m8-eval-template-list-layout.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - web/src/pages/EvalTemplatesPage.tsx
  - web/src/styles/templates.css
  - prototype/**
  - plans/**
  - .agents/flow/issues/m8-eval-template-list-layout.md
  - .agents/flow/issues/m8-eval-template-list-layout.en.md
  - .agents/flow/issues/m8-eval-template-list-layout.i18n.yaml
adr: []
github:
  number: 386
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/386
```

## 背景

生产「评测模板」列表在长模板名称下发生列宽塌陷：模板列被撑到 658px，固定组合列被压到 71px，固定组合三行内容逐词换行、行高 332px；原型在相同数据与 1500×837 视口下保持 54px 行高。根因是生产表格使用 antd `Table` 默认的 `table-layout: auto`，模板名称渲染成 `Button type="link"`（默认 `white-space: nowrap`），模板/固定组合列又没有列宽或截断约束；原型使用的固定表格布局、固定列宽与单行省略没有迁移到 React 实现。

生产比原型多一个「费用估算」列（8 列 vs 7 列）。为保留列表提前发现未定价模板的能力，本 issue 保留该列并同步补进原型，而不是删除生产列。

## 目标

- antd `Table` 改为固定表格布局，8 列都声明宽度，并给窄屏提供横向滚动，避免列宽被内容反推。
- 模板名称单行省略、描述最多两行；固定组合三行都单行省略，并用 `title` 暴露完整值。
- 单元格内容顶部对齐，行高回到紧凑范围，操作按钮不逐词换行。
- 原型同步补上「费用估算」列并重新分配 8 列宽度，与生产实现保持同一设计。
- 用 1500×837、长名称模板的修改前后截图和原型截图验证，不把冒烟截图提交进代码仓。

## 验收

- 1500×837 视口、与生产相同的长名称模板下，模板列约 240px、固定组合列约 300px，行高从 332px 降到 74px 左右。
- 模板名称、描述与固定组合都有省略号；悬停能看到完整名称/描述/组合；操作列按钮不逐词换行。
- 窄屏出现横向滚动，不压塌固定组合列。
- 原型在相同数据下呈现同样的 8 列结构与截断效果；修改前后截图同视口、同主题并附在实现 PR。
- `npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与 `web` 构建通过。

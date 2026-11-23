# [m10] 评测任务快速启动区与原型不一致

[English](m8-eval-quick-start-row.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - web/src/pages/EvalsPage.tsx
  - web/src/components/TemplateBits.tsx
  - web/src/styles/templates.css
  - plans/**
  - .agents/flow/issues/m8-eval-quick-start-row.md
  - .agents/flow/issues/m8-eval-quick-start-row.en.md
  - .agents/flow/issues/m8-eval-quick-start-row.i18n.yaml
adr: []
github:
  number: 388
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/388
```

## 背景

生产「评测任务 / 快速启动」区与 `prototype` 存在视觉漂移：模板名称是 antd 蓝色链接（12px / 650），原型是深色 13px / 700 标题；最近启动是纵向纯文本「从未启动」+「—」，原型是中性状态标签 + 时间同行；操作区多出「启用」标签；行内边距、分隔线与组合行字号也不同。长模板名称在窄视口会出现省略或溢出，1200px 下曾直接压住相邻列。根因是 M10 #378 在 React 中重写快速启动行时没有迁移原型的 `quickTemplateRow` 与 `.quick-template-*` 样式。

## 目标

- 名称改为原型样式的文本按钮：深色 13px / 700、可换行、hover 主色，不再使用 antd 蓝色链接按钮。
- 最近启动改为状态标签 + 时间同行；无运行时显示中性「尚未启动」标签。
- 快速启动行移除「启用」标签，只保留「详情 / 启动」；模板状态继续在完整列表展示。
- 恢复原型的行样式：`padding: 11px 0`、列表 `0 19px 14px`、虚线分隔、组合行 10px。
- `≤1240px` 隐藏最近启动列，名称在列内换行或不覆盖相邻列。
- 用长名称模板在 1500px 与 1200px 增加截图或 DOM 断言（行内无重叠、1200px 隐藏最近启动），截图不提交进代码仓。

## 验收

- 1500×837 下快速启动行高约 60px、名称为深色 13px / 700、最近启动为标签 + 时间同行、操作区无「启用」。
- 1200×837 下最近启动隐藏、名称不覆盖相邻列、无水平重叠。
- 原型在相同数据与视口下呈现同一结构；修改前后截图同视口、同主题并附在实现 PR。
- `npm test`、`npm run verify:flow`、`npm run verify:docs` 与 `web` 构建通过。

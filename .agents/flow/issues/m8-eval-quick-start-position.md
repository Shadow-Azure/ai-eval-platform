# [m10] 评测任务快速启动区位置与原型不一致

[English](m8-eval-quick-start-position.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - web/src/pages/EvalsPage.tsx
  - plans/**
  - .agents/flow/issues/m8-eval-quick-start-position.md
  - .agents/flow/issues/m8-eval-quick-start-position.en.md
  - .agents/flow/issues/m8-eval-quick-start-position.i18n.yaml
adr: []
github:
  number: 409
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/409
```

## 背景

生产「评测任务」页把「快速启动」区块放在 4 个统计卡之前；原型 `prototype/app.js` 的 `evalsPage()` 顺序是页头 → 4 个统计卡 → 快速启动 → 评测列表。用户在 #388 修复行样式后对比原型，指出区块位置仍不一致。这是 #388 未覆盖的页面结构差异，行样式本身已经对齐。

## 目标

- 把快速启动区块移动到统计卡之后、评测列表之前，顺序与原型一致。
- 列表加载失败时快速启动仍然可见，不因顺序调整而消失。
- 用同视口、同主题的修改前后截图与原型截图验证。

## 验收

- 1500×837 下页面顺序为：页头 → 4 个统计卡 → 快速启动 → 评测列表，与原型一致。
- 快速启动行样式保持 #388 的结果：深色 13px / 700 名称、标签 + 时间同行、仅详情 / 启动、虚线分隔。
- 1200×837 下顺序不变，最近启动隐藏、无横向滚动。
- `web` 构建、`npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。

# [m9] 主 Agent session id 支持一键复制

[English](m9-session-id-copy.en.md) | 中文

```yaml flow
kind: issue
milestone: m9
priority: P0
status: done
scope:
  - web/src/**
  - tests/**
  - prototype/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 285
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/285
```

## 背景

#277 已在用例明细展示主 Agent session id，但只显示 `8…4` 截断值，复制完整值需要悬停或手工选择。希望增加复制按钮，一键复制完整原生 session id。

## 目标

- 有 session id 的单元格右侧增加复制按钮，复制完整原生 session id，不是截断值或 attempt id。
- 复制不触发行跳转；无会话的 `—` 不显示按钮；按钮可键盘聚焦并有明确名称。
- 复制成功/失败有短暂提示；先改原型并附修改前后截图，再落生产前端。

## 验收

- 原型点击复制成功、toast 提示且 URL 不变化；无会话行无按钮。
- 生产前端复制完整 `main_session_id`，失败有降级提示；筛选、分页、行点击与轨迹入口不回归。
- 后端无需改动；前端构建、`npm test`、`verify:docs`、`verify:flow` 通过；前后截图回贴 issue #285。

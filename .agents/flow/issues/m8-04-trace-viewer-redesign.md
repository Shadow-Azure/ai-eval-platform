# 轨迹查看体验重设计：对话轨迹与轮次时间线

[English](m8-04-trace-viewer-redesign.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-04-trace-viewer-redesign.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/pages/TracePage.tsx
  - web/src/styles/trace-viewer.css
  - plans/**
  - prototype/**
adr: []
github:
  number: 182
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/182
```

## 背景

轨迹页已有「会话树 + waterfall + span 属性」三栏骨架，但真实轨迹的阅读成本很高：76 个 span 平铺、75 个起止时间相同显示 0ms、每次工具调用产生重复行，缺少对话层与轮次导航。

## 目标

- 增加对话轨迹与轮次时间线作为默认阅读入口，保留原始 span / 属性 / 原始文件入口。
- 轮次按 LLM 调用与工具边界在前端归并，不要求后端新增表或字段；零宽 span 不伪造耗时。
- 先完成可交互原型与改动前后截图，确认方向后再进入前端开发。

## 验收

- 进入轨迹页默认看到对话轨迹与轮次卡片，可展开 / 折叠；选中轮次时右栏联动。
- 时间线能表达轮次窗口与时间去向；零宽 span 明确标注而不显示误导性的 0ms 条。
- 保留现有会话树、类型筛选、深链、原始文件与完整 span 内容入口；`web` 构建与类型检查通过。

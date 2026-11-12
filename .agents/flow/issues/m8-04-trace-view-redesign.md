# m8-04 会话轨迹查看体验重设计

[English](m8-04-trace-view-redesign.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-04-trace-view-redesign.*
  - .agents/flow/issues/m8-03-eval-records-pagination.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/**
  - plans/**
adr: []
github:
  number: 182
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/182
```

## 背景

【评测 / 会话轨迹】当前把 76 个 span 平铺成三栏：会话树、waterfall、属性表。真实轨迹中 75 / 76 个 span 的起止时间相同，`duration_ms` 为空，瀑布退化成零宽竖线；同时每次工具调用产生重复的 message / tool_result 行，用户需要在脑中把 `LLM → thinking → Bash → tool_result` 还原成一次操作。M8-04 在既有三栏骨架上重设计轨迹查看体验，不扩展后端数据模型。

## 目标

- 默认提供“对话轨迹”视图：按阶段归并 span，轮次可折叠，展示每一步调用的工具、目标与状态。
- 提供“步骤”视图：步骤默认折叠，按工具类型展开 Bash 命令与输出、Edit / Write diff、Read 路径与内容、Skill / Subagent 嵌套步骤。
- 提供阶段时间线：按阶段窗口表达时间去向；零宽 span 明确标注，不伪造耗时。
- 右栏提供可读详情与 `概览 / 输入 / 输出 / 属性 / 原始` 逐层下钻；保留原始文件、完整 span 与受控下载。
- 保留现有 attempt / session / span / kind 深链、质量提示、加载与错误状态。

## 验收

- 进入轨迹页默认看到对话轨迹与阶段卡片，可折叠 / 展开；选中步骤时右栏联动。
- 步骤视图默认全部折叠；Bash、Edit / Write、Read、Skill / Subagent 展开后分别看到命令与输出、diff、路径与内容、嵌套步骤。
- 阶段时间线表达时间去向；零宽 span 有明确说明，不显示为误导性的 0ms 条。
- 搜索、类型筛选、错误聚焦至少有一项可用；现有深链与筛选行为保留。
- `web` typecheck / build 通过；PR 附改动前后同场景截图与冒烟报告。

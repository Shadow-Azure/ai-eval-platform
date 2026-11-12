# 总览最近评测接入真实数据并每页 5 条分页

[English](m8-02-recent-evals-pagination.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-02-recent-evals-pagination.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/api/overview.ts
  - web/src/pages/OverviewPage.tsx
  - web/src/pages/EvalsPage.tsx
  - web/src/utils/evaluationDisplay.ts
  - web/src/styles/overview.css
  - plans/**
adr: []
github:
  number: 180
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/180
```

## 背景

生产总览页「最近评测」始终显示“暂无评测任务”，但评测任务页有 35 条记录。原因：`web/src/pages/OverviewPage.tsx` 的 Table 固定传入 `dataSource={[]}`，从未请求评测列表；`/api/v1/overview` 只返回计数，不返回最近条目；表头使用的扁平字段与 `EvaluationSummary` 不一致。2026-11-08 的 PR #19 检视已记录“最近评测表固定空数组，M2-05 接入 API 后替换”，后续没有跟进。M8-02 需接入真实数据并每页 5 条分页，先修复缺陷，不扩展新功能。

## 目标

- 总览「最近评测」读取 `/api/v1/evaluations`，默认按创建时间倒序，与服务端分页一致。
- 每页固定 5 条，默认第 1 页，可翻页浏览全部记录。
- 保留现有列并补齐真实字段映射：评测任务、Agent / 模型、数据集、状态、进展、核心指标、开始时间；行尾对齐原型增加详情入口。
- 加载、失败和真正空数据分别有明确反馈；移除过时的“M3 建设中”标记。

## 验收

- 生产 35 条记录时第 1 页显示 5 条、分页共 7 页；第 2 页显示第 6–10 条；总条数与评测任务页一致。
- 记录数不超过 5 条时不出空白页；0 条时才显示“暂无评测任务”；请求失败可重试。
- `web` 的 typecheck/build 通过；PR 附改动前后同场景截图。
- 冒烟测试覆盖总览分页与详情跳转，结果和原型截图对照后放入 PR 描述。

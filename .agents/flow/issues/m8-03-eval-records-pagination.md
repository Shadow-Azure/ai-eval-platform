# m8-03 评测任务记录按每页 10 条分页

[English](m8-03-eval-records-pagination.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-03-eval-records-pagination.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/pages/EvalsPage.tsx
  - plans/**
adr: []
github:
  number: 181
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/181
```

## 背景

生产评测任务列表当前把全部记录平铺在一页内（`web/src/pages/EvalsPage.tsx` 中 `pagination={false}`）。2026-11-12 生产环境有 35 条任务，1440 宽视口下页面高约 3168px（约 3.5 屏），列表末尾没有分页控件，浏览与定位需要长距离滚动。

## 目标

- 评测任务列表分页展示，每页固定 10 条记录，默认展示第 1 页。
- 保留现有列、筛选栏、统计卡与自动刷新。
- 分页只改变展示，不改变数据来源与筛选语义；筛选变化后回到第 1 页。

## 验收

- 记录数超过 10 条时只展示 10 条，并可通过分页器浏览全部记录；总数与页码正确，末页不足 10 条时正常展示。
- 记录数不超过 10 条时正常展示，不出现空页。
- 搜索、状态筛选、数据集筛选变化后分页回到第 1 页，筛选结果的总数与页码正确。
- 现有列、状态标签与自动刷新（当前 3 秒）行为不变。
- `web` 构建与类型检查通过；PR 附改动前后同场景截图。

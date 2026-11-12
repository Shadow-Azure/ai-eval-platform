# m8-01 Runner 记录按每页 5 条分页

[English](m8-01-runner-records-pagination.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-01-runner-records-pagination.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/pages/OperationsPage.tsx
  - web/screenshots/**
adr: []
github:
  number: 178
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/178
```

## 背景

运营状态 → 执行资源的 Runner 表当前一次平铺全部记录（`web/src/pages/OperationsPage.tsx` 中 `pagination={false}`）。生产环境当前有 44 台 Runner，页面被表格拉得很长，浏览与定位 Runner 不便；需要改为分页显示，每页 5 条。

## 目标

- Runner 表分页展示，每页固定 5 条记录，默认展示第 1 页。
- 保留现有 Runner、资源池、状态、最近心跳与标签列及状态标签。
- 保留运营状态页 30 秒自动刷新；分页只改变展示，不改变数据来源。

## 验收

- 记录数超过 5 条时只展示 5 条，并可通过分页器浏览全部记录；总条数与页码正确，末页不足 5 条时正常展示。
- 记录数不超过 5 条时正常展示，不出现空页。
- 现有列、状态标签与 30 秒自动刷新行为不变。
- `web` 构建与类型检查通过；PR 附改动前后同场景截图。

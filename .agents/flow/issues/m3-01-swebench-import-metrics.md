# SWE-bench 数据切片导入与指标注册

[English](m3-01-swebench-import-metrics.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: done
scope:
  - .agents/flow/issues/m3-01-swebench-import-metrics.*
  - backend/**
  - tests/**
adr: []
github:
  number: 45
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/45
```

## 背景

M3 要让一个真实用例完成 Agent 执行、评分与结果查询，前提是平台内存在可导入、可校验、可浏览的 SWE-bench 数据切片，以及版本化的指标口径。数据集/版本分离、适配器注册与不可变任务视图对应设计需求 2 与 2.1；指标定义必须作为版本化注册项，聚合器只允许平台白名单（决策 D10），SWE-bench 主指标采用官方 `% Resolved = resolved / total` 口径。

## 目标

- 实现 SWE-bench 数据集适配器，按设计文档 07 §1.1 完成标准映射。
- 提供数据集版本导入与校验的后端流程，任务视图不可变，重复导入同版本幂等。
- 提供数据集目录、版本详情、用例详情与指标定义的查询 API，服务端分页。
- 注册 SWE-bench 官方口径指标与通用版本化指标配置，聚合器来自平台白名单。

## 验收

- 一个 SWE-bench Verified 数据切片可导入并通过校验，用例总数与版本一致。
- 指标定义作为版本化注册项可查询，口径说明可见，白名单外聚合器不执行。
- 重复导入同一版本返回幂等结果，不产生第二个版本或脏任务。
- 导入校验失败（缺字段、坏 patch）有明确失败分类与可诊断错误，pytest 覆盖成功与失败路径。

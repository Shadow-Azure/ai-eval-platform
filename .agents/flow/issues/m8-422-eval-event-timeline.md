# [m8-422] 评测详情「实时事件」渲染可读时间线

[English](m8-422-eval-event-timeline.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-422-eval-event-timeline.*
  - plans/**
  - tests/eval-event-timeline.test.mjs
  - web/src/features/evaluations/eventTimeline.ts
  - web/src/pages/EvalDetailPage.tsx
  - web/src/styles/eval-detail.css
adr: []
github:
  number: 422
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/422
```

## 背景

生产「评测详情 → 实时事件」把 `trace.ingestion.updated` 的 `files` 对象清单直接铺在卡片里：`eventSummary()` 没有该事件类型的映射，落到默认分支按 `key=value` 拼接 payload 前三个键，而 PostgreSQL JSONB 不保证键顺序，于是整段 JSON 占满卡片。概览又只取最近 6 条，「轨迹入库」快照与心跳会把用例开始、完成、失败、派发等关键事件挤出可视区。

## 目标

- 实时事件只展示人类可读摘要，长 payload 收敛为计数或单行摘要，不再出现 `files=` 与对象数组。
- `trace.ingestion.updated` 映射为「轨迹入库：N 个文件 ready / pending / failed」并保留轨迹状态与 attempt 上下文。
- 概览时间线对齐原型：事件主体 + 动作 + 上下文一行 + 右侧时间，圆点与连线组成时间线。
- 概览按关键调度 / 用例事件挑选，同一 attempt 的重复入库快照只保留最新一条，心跳不占概览名额。
- 「查看全部」的事件日志仍展示完整事件，摘要同样可读。

## 方案

- 新增 `web/src/features/evaluations/eventTimeline.ts`：集中事件类型标签、单事件摘要投影与概览时间线挑选；未知类型只读取标量字段，绝不序列化嵌套对象。
- `trace.ingestion.updated` 从 `counts` / `file_counts` 读取 `expected`、`ready`、`pending`、`degraded`、`failed`，动作行输出计数，上下文行输出轨迹状态、attempt 与错误摘要。
- 概览从最新事件向前挑选白名单内的关键事件，入库快照按 `(case_run_id, attempt_no)` 去重；有关键事件时最多保留 2 条入库快照，否则用入库快照填满。
- `EvalDetailPage` 的概览卡片改为时间线结构（圆点、连线、主体、上下文、`HH:MM`），事件日志表格复用同一摘要；样式按原型 `timeline-item` 的网格与圆点尺寸实现。
- 新增 Node 测试覆盖入库摘要、未知 payload、时间线挑选与时间格式。

## 验收

- 含 `trace.ingestion.updated` 的完成态评测打开实时事件，卡片不再出现 `files=` 或整段 JSON。
- 概览可读到用例 / 分片摘要、上下文与时间，重复入库快照不会占满 6 条名额。
- 「查看全部」仍可看到完整事件日志。
- 1500×837 与 1200×837 下卡片无横向滚动，单条事件高度稳定。
- `npm test`、`npm run verify:flow`、`npm run verify:docs`、`web npm run build` 通过。

## 范围

- 涉及：评测详情事件摘要映射、概览时间线结构与样式、前端回归测试、流程 issue 条目。
- 不涉及：事件接口、后端事件写入、事件流连接逻辑、用例明细与结果页。

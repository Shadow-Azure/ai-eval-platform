# [m9] 用例明细列表展示主 Agent session id

[English](m9-case-session-id.en.md) | 中文

```yaml flow
kind: issue
milestone: m9
priority: P2
status: ready
scope:
  - backend/app/**
  - tests/**
  - web/src/**
  - prototype/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 277
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/277
```

## 背景

评测详情「用例明细」只展示执行、结果、轨迹、测试打点与耗时，没有主 Agent session id。排查 bad case 上报和 harness-evolution 回链时只能逐条打开轨迹页找原生会话 id，无法在列表层与 HE `sessionId` 对照。`trace_session` 已按 attempt 保存根会话的 `native_session_id`，无轨迹用例没有原生会话。原型修改前后截图见 issue #277。

## 目标

- 用例明细新增「主 Agent session id」列，展示 latest attempt 主 Agent 的原生 session id；无轨迹显示 `—`。
- 列表接口按分页批量返回主 Agent session id，不新增 N+1 查询。
- 长值截断、悬停查看完整值；现有筛选、分页、CSV 导出、行点击与「轨迹」入口保持不变。
- 同步接口与架构现状文档，并补后端和前端测试。

## 验收

- 列表接口每行返回主 Agent session id（建议 `main_session_id`），与 latest attempt 根会话 `native_session_id` 一致；无轨迹或未解析为 `null`。
- 前端列正确显示截断值与完整值提示，空态为 `—`；窄屏横向滚动不破版，既有交互不回归。
- 原型与生产页面口径一致；原型修改前后截图已回贴 issue #277。
- `npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与相关后端测试通过。

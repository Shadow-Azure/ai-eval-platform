# [m9] 轨迹页展示 attemptId 与主 Agent sessionId 双行标识

[English](m9-trace-identifiers.en.md) | 中文

```yaml flow
kind: issue
milestone: m9
priority: P0
status: done
scope:
  - backend/app/**
  - tests/**
  - web/src/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 284
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/284
```

## 背景

轨迹页顶部标签行后只显示 `eval_case_attempt.id`，没有前缀，容易被误认为 sessionId；左侧「会话」树显示的是平台 `TraceSession.id` 前 12 位，原生主 Agent sessionId 没有展示。交互原型与修改前后截图已由 issue #284 确认，本 issue 把同一双行标识落到生产前端。

## 目标

- 轨迹页顶部标识区改为两行，分别带 `attemptId:`、`sessionId:` 前缀，沿用现有小号等宽字体。
- sessionId 取该 attempt 主 Agent 根会话的原生 `native_session_id`（`parent_session_id IS NULL`、`status != unlinked`，按 `ordinal, id` 取第一个），与自进化上报 `_main_session_id()` 同语义；不取左侧当前选中会话。
- 无轨迹或未解析到主会话时，`sessionId:` 显示 `—`，悬停提示“未解析到主 Agent 会话（该 attempt 没有可用轨迹）”；attemptId 保留，现有无轨迹告警不变。
- 超长 id 截断并用悬停展示完整值；窄屏允许换行，不挤压右侧耗时/tokens/成本统计。
- 补后端与前端测试，并同步架构现状文档。

## 验收

- 轨迹页顶部显示两行带前缀的 attemptId / 主 Agent sessionId，且不随左侧选中会话变化。
- 无轨迹时 sessionId 为 `—`，可悬停查看原因，attemptId 仍显示。
- 生产前端视觉与原型一致，修改前后截图同视口、同主题、同路由并附在实现 PR。
- `npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与相关后端测试通过。

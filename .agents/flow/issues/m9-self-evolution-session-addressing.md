# 自进化会话包在主/子 Agent 场景下寻址错误

[English](m9-self-evolution-session-addressing.en.md) | 中文

```yaml flow
kind: issue
milestone: m9
priority: P0
status: done
scope:
  - backend/app/**
  - tests/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 338
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/338
```

## 背景

Claude Code 主 Agent 与全部子 Agent 共用根 `sessionId`，而会话包端点把 `TraceSession.native_session_id` 当唯一键并按入库时间取最新一行：根请求可能返回子 Agent，`childSessionIds` 重复为根 id，未挂载（`unlinked`）子 Agent 会被当成 `main`。单 Agent 会话不受影响。

## 目标

- 根请求在 run 作用域内选择 `parent_session_id IS NULL`、`status != 'unlinked'`、ordinal 最小的根行，找不到时 404，不把任何子行当 `main`。
- 子请求按全局唯一的 `TraceSession.id` 解析；`unlinked` 行返回 404，`childSessionIds` 与更深层 `parentSessionId` 均使用 `TraceSession.id`，直接父级是根的节点其 `parentSessionId` 仍使用 native 根 id。
- 选择查询带 run 作用域，跨 run 的同 native id 不再抢占正确行；`agentId` 只保留在 `agentMeta` 展示。
- 用真实形态（共享 native id、depth=2、unlinked、跨 run、单 Agent 回归）补齐测试，并同步设计 04 §8。

## 验收

- 共享 native id 的根请求返回 `agentKind=main`、`parentSessionId=null`，`childSessionIds` 为已关联子行的 `TraceSession.id` 且按 ordinal 排序。
- 子请求按 `TraceSession.id` 返回 `subagent` 且 `parentSessionId` 为根 native id；depth=2 的 `parentSessionId` 为父 `TraceSession.id`。
- `unlinked` 行不会作为 `main` 返回；跨 run 同 native id 返回签名 run 内的行；单 Agent 回归返回 `childSessionIds=[]`。
- 相关 pytest、`npm run verify:flow` 与文档门禁通过。

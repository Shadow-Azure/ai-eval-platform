# Fix session-package addressing for main and subagent sessions

English | [中文](m9-self-evolution-session-addressing.md)

```yaml flow
kind: issue
milestone: m9
priority: P0
status: in-progress
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

## Background

Claude Code's main Agent and every subagent share the root `sessionId`, but the session-package endpoint treated `TraceSession.native_session_id` as a unique key and picked the newest row: a root request could return a subagent, `childSessionIds` repeated the root id, and an unlinked subagent was returned as `main`. Single-Agent sessions are unaffected.

## Goals

- Resolve the root request inside the run scope to the root row (`parent_session_id IS NULL`, `status != 'unlinked'`, lowest ordinal); answer 404 when there is none instead of returning a child row as `main`.
- Resolve subagent requests by the globally unique `TraceSession.id`; unlinked rows answer 404, `childSessionIds` and deeper `parentSessionId` values use `TraceSession.id`, and a node whose direct parent is the root keeps the native root id as `parentSessionId`.
- Scope selection by run so the same native id in another run cannot win; keep `agentId` as `agentMeta` display only.
- Cover the real shapes (shared native id, depth=2, unlinked, cross-run, single-Agent regression) and sync design 04 §8.

## Acceptance

- A root request with a shared native id returns `agentKind=main` and `parentSessionId=null`; `childSessionIds` are the linked children's `TraceSession.id` in ordinal order.
- A subagent request by `TraceSession.id` returns `subagent` with the native root id as `parentSessionId`; a depth=2 node reports its parent `TraceSession.id`.
- Unlinked rows are never returned as `main`; a cross-run shared native id resolves to the signed run; the single-Agent regression returns `childSessionIds=[]`.
- The related pytest suite, `npm run verify:flow`, and the documentation gates pass.

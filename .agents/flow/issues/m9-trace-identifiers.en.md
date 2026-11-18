# [m9] Show attemptId and main Agent sessionId as two lines on the trace page

English | [中文](m9-trace-identifiers.md)

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

## Background

The trace page header showed only `eval_case_attempt.id` after the tag row, without a prefix, so it was easy to mistake it for the sessionId; the left "sessions" tree showed the first 12 characters of the platform `TraceSession.id`, and the native main Agent sessionId was not shown. The interaction prototype and before/after screenshots were confirmed in issue #284; this issue lands the same two-line identifier in the production frontend.

## Goals

- Replace the trace page header identifier with two lines prefixed `attemptId:` and `sessionId:`, keeping the existing small monospace typography.
- Take sessionId from the attempt's main Agent root session native `native_session_id` (`parent_session_id IS NULL`, `status != unlinked`, first by `ordinal, id`), with the same semantics as the self-evolution `_main_session_id()` lookup; do not follow the selected session in the left tree.
- When there is no trace or the main session is unresolved, render `—` with the tooltip "未解析到主 Agent 会话（该 attempt 没有可用轨迹）"; keep attemptId and the existing no-trace warning unchanged.
- Truncate long ids and expose the full value on hover; allow wrapping on narrow screens without squeezing the duration/tokens/cost summary.
- Add backend and frontend tests and update the architecture current-state docs.

## Acceptance

- The trace page header shows two prefixed lines for attemptId and the main Agent sessionId, and the sessionId does not change when the left session selection changes.
- Without a trace, sessionId is `—` with the reason on hover while attemptId remains visible.
- The production frontend matches the prototype; before/after screenshots use the same viewport, theme and route and are attached to the implementation PR.
- `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs` and the relevant backend tests pass.

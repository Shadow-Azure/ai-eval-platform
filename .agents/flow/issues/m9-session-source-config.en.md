# Self-evolution reporting sessionSource configuration, backfill and docs alignment

English | [中文](m9-session-source-config.md)

```yaml flow
kind: issue
milestone: m9
priority: P2
status: ready
scope:
  - .github/**
  - backend/app/**
  - tests/**
  - docs/**
  - .agents/flow/**
  - plans/**
adr: []
github:
  number: 274
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/274
```

## Background

All five bad cases of evaluation `c6b873b5-b01a-4074-8729-08762fcd3eef` reported successfully (HTTP 201), but `SELF_EVO_SESSION_SIGNING_KEY` and `SELF_EVO_PUBLIC_BASE_URL` were empty, so the payloads carried no `sessionSource` and every HE root sync failed with `SESSION_QUERY_UNAVAILABLE`. The public base URL can be derived from the existing `ECS_HOST`; the signing key needs a dedicated random secret. Existing succeeded payloads are frozen and will not be repaired by changing the configuration.

## Goals

- Derive `SELF_EVO_PUBLIC_BASE_URL` from `ECS_HOST` in `deploy.yml` (`http://${ECS_HOST}:8080`), while an explicit secret still wins.
- Add a `backfill-self-evolution` command that rebuilds `sessionSource` for existing succeeded reports with a native session and resets them to pending for the worker.
- Fix the `not_scored` wording in issue #263 and document the `sessionId` fallback semantics in the architecture page.
- Add deployment contract tests and backfill unit tests.

## Acceptance

- The deployment contract test asserts the `ECS_HOST` fallback for `SELF_EVO_PUBLIC_BASE_URL` and explicit override precedence.
- The backfill command only touches succeeded reports with a native session; no-trace, already-sourced and unconfigured rows are counted separately and left unchanged.
- Chinese and English docs mirror each other; `verify:flow`, `lint:docs`, `verify:docs` and the relevant pytest suite pass.

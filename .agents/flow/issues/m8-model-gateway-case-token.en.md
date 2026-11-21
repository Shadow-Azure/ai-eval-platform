# Scope model gateway tokens to case attempts

English | [中文](m8-model-gateway-case-token.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/**
  - backend/app/**
  - backend/migrations/**
  - deploy/**
  - docs/**
  - plans/**
  - runner/**
  - tests/**
adr: []
github:
  number: 358
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/358
```

## Background

In production run `5a20991f`, six cases shared one shard and one shard+attempt-scoped model token; the gateway looked up an attempt by `(run, shard, attempt)` and took `.first()`, so after one case finished, a still-running case could be rejected with a false 409 stale_attempt. See #358.

## Goal

- Bind model-token authorization and revocation to a case attempt; cases in the same shard do not affect each other.
- The gateway looks up the exact case attempt and rejects only when that case's attempt is terminal; run-level cancellation and budget pause still apply globally.
- During the compatibility window, accept only legacy tokens without case scope and validate them with the shard-wide all-terminal rule; new tokens must carry case scope.
- Update the design document and model-gateway contract, and add a regression test for multiple cases in one shard.

## Acceptance

- A terminal case in the same shard no longer affects another running case; 409 appears only when the caller's own attempt is terminal.
- The legacy compatibility branch remains until its cutoff and is then removed; new tokens without case scope fail closed.
- The design document matches the implementation and the regression test passes.

# Fetch per-case manifests over a fresh connection

English | [中文](m8-runner-manifest-fresh-connection.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - .agents/flow/**
  - plans/**
  - runner/**
  - tests/**
adr: []
github:
  number: 362
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/362
```

## Background

After #360 was deployed, two production evaluation runs failed all six cases with `runner_failed / model_token_unavailable` and zero token usage. After the `started` callback the runner blocks its event loop with `spawnSync` while loading the image tarball (about 42 seconds on a cold host) and checking out the base commit; uvicorn closes idle keep-alive connections after its default five seconds, and the runner never processed the server FIN. The per-case manifest refresh then reused that closed connection, `fetch` failed immediately with `UND_ERR_SOCKET`, and the #360 fail-closed rule turned that transport failure into a case failure. The GitHub issue is a P0 fix; the flow tree records P2, the currently unlocked tier in m8, so completed m9 P0 issues do not trip the cross-milestone serialization gate.

## Goal

- Fetch the per-case manifest over a one-shot, non-pooled connection that opens a fresh TCP connection every time and does not depend on a keep-alive socket still being alive.
- Include `error.cause.code` in the `model_token_refresh_failed` log and the case error summary when the refresh fails.
- Keep fail-closed behavior: never start the Agent without a case token and never fall back to the shard token.
- Add a regression test that simulates "server closes the idle connection + client blocks synchronously".

## Acceptance

- The simulated regression test passes: the refresh succeeds after the server closed the idle connection while the client blocked synchronously, and the two requests use different connections.
- A failing refresh still fails closed without starting the Agent, and the error summary carries the transport cause.
- Runner contract tests and the related flow gates pass.

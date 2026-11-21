# Async runner image download and checkout: keep long tasks off the event loop

English | [中文](m8-runner-async-io.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - runner/**
  - tests/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 363
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/363
```

## Background

- #362 found the direct cause of production `model_token_unavailable`: the per-case manifest refresh reused a keep-alive connection the platform had already closed; before the refresh, the runner downloads/loads the image tarball (about 42 seconds on a cold runner) and runs `git checkout` (about 5 seconds) through `spawnSync`, blocking the Node event loop so undici never sees the server's FIN.
- #362's P0 removed that concrete failure with a one-shot, non-pooled connection; this issue hardens the root cause: long tasks must not block the runner event loop, which also avoids similar races and delayed cancellation/diagnostics.
- While a synchronous child runs, the main process cannot process network I/O, cancellation or callbacks; the heartbeat has a separate sidecar, but control-plane requests from the main process are still delayed.

## Goals

- Add `runner/lib/run-command.mjs`: a Promise wrapper over `child_process.spawn` with `timeout`, `maxBuffer`, `AbortSignal`, stdout/stderr capture, non-zero exit status and error-code mapping.
- Make `loadImageTarball` async and run the download and `docker load` asynchronously; failures still return `{ok:false, stage, error}` and keep `trace/image-tarball.json`.
- Run `real-task.mjs`'s `git checkout` and recursive `chmod` through awaited async commands; failures still take the original error path.
- Keep the event loop responsive during image download/load while preserving the existing timeout, output capture, diagnostics and exit-code semantics.

## Non-goals

- Do not change the token refresh protocol, manifest endpoints or gateway authorization.
- Do not change the workflow / dispatch structure.
- No blanket migration of every `spawnSync`: short, fast calls that must complete synchronously during startup may stay, but each needs an explicit evaluation.

## Acceptance

- On a cold image, the event loop stays responsive during the image download (verified by timer drift or log timestamps).
- Image download/load and checkout keep the same failure diagnostics, timeout and error-code behavior as before.
- `node --test tests/runner/*.test.mjs` is green.
- Combined with #362's P0, a production cold-image rerun no longer reports `model_token_unavailable`.

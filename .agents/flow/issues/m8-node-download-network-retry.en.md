# [m8] Retry the Runner Node download once after a transient network failure

English | [中文](m8-node-download-network-retry.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-node-download-network-retry.*
  - runner/adapters/runtimes/claude-code.mjs
  - tests/runner/node-download-retry.test.mjs
adr: []
github:
  number: 397
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/397
```

## Background

At 10:50 CST on 2026-11-23 the only case of evaluation `de8e73cc-1ff5-4bcf-a53c-7b2117f60d7e` failed during environment setup: `installScript()` was downloading Node 22.14.0 from `registry.npmmirror.com` with curl when the TLS read was reset by the peer, so curl exited with 56. `docker exec` passed that status back to the runner, which folded it into a permanent `agent_crashed`; there was no retry and the case ended immediately.

## Goal

- Retry the Node tarball download at most once, only inside `installScript()` in `runner/adapters/runtimes/claude-code.mjs`.
- Retry only when curl exits with a confirmed network code (6, 7, 28, 35, 52, 55, 56); exit immediately on deterministic failures such as HTTP errors, certificates, write failures and size mismatches.
- When the second attempt also fails, keep its stderr and exit code and continue through the existing failure chain; do not change `dockerRun`, failure classification, `max_attempts`, caching or timeouts.

## Acceptance

- Each of the 7 network exit codes is retried exactly once and then lets the install continue; each of the 5 deterministic exit codes is not retried and keeps its original exit code.
- A second network failure ends with the second exit code and stderr, without running later install steps.
- `node --test tests/runner/*.test.mjs`, `npm test`, flow and documentation gates pass.

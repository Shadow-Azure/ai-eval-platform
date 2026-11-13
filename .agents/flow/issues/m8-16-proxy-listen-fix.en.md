# [m8-16] Fix the proxy container not listening and emit proxy diagnostics during deploy

English | [中文](m8-16-proxy-listen-fix.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-16-proxy-listen-fix.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/**
  - tests/deploy/**
  - docs/**
  - plans/**
adr: []
github:
  number: 213
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/213
```

## Background

- After #212 the deploy succeeded and the proxy container showed Started, but 3129 refused connections from both the container and outside: squid was not listening (most likely the image's default command daemonizes and the container exits), so the evaluation install failed.
- The health check only covers the API `/readyz`; the proxy is unchecked, so a failure left only a Started line without diagnostics.

## Goals

- Force squid into the foreground in compose: `command: ["-N", "-f", "/etc/squid/squid.conf"]`.
- After the rolling update emit the proxy's `ps`, `logs --tail=120`, 3128/3129 port probes and a 3129 no-token probe (expect 407) so the CI log carries the diagnosis.
- Keep the 3128 private / 3129 authenticated semantics unchanged.

## Acceptance

- The deploy log shows the proxy state, logs and port checks; 3129 listens; no-token requests return 407.
- A production evaluation installs through the proxy and runs design→propose→apply.

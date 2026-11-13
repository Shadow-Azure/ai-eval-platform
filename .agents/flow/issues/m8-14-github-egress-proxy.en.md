# [m8-14] Platform forward proxy for GitHub access from evaluation containers

English | [中文](m8-14-github-egress-proxy.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-14-github-egress-proxy.*
  - .agents/flow/issues/m8-12-case-token-ttl.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - backend/app/config.py
  - backend/app/api/runners.py
  - runner/scripts/real-task.mjs
  - runner/lib/task-payload.mjs
  - scripts/doc-budgets.manifest.json
  - tests/backend/**
  - tests/runner/**
  - deploy/**
  - docs/**
  - plans/**
adr: []
github:
  number: 209
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/209
```

## Background

- Evaluation containers use `--network host`, so their network matches the runner host; the host's egress to GitHub (especially the release-asset domains) is restricted or unreliable, and the CLI install fails with `ETIMEDOUT` downloading the tarball from a GitHub Release.
- Decision: use a platform forward proxy with an allowlist instead of opening all container egress; containers reach GitHub through `HTTPS_PROXY`.
- The platform backend ECS has stable GitHub egress, so the proxy lives there and the runner reaches it over the private network.

## Goals

- Add a backend setting `RUNNER_EGRESS_PROXY` (default empty) delivered with the evaluation task manifest/payload.
- The Runner injects `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` into the asset-install and agent exec environments; behavior is unchanged when unset.
- Deploy an allowlisted forward proxy (squid/tinyproxy) on the backend ECS on a private port reachable only from the runner; only GitHub domains are allowed and everything else is denied and logged.
- Allowlist: `github.com`, `api.github.com`, `codeload.github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`, `github-releases.githubusercontent.com`, `*.githubusercontent.com`.
- Tests: runner injection/no-injection, payload carrying the proxy, backward compatibility without configuration; deployment docs describing allowlist maintenance and verification commands.

## Acceptance

- The evaluation container downloads the CLI through the proxy; design→propose→apply runs and the trace is preserved.
- Behavior is unchanged when no proxy is configured; CI passes.
- The proxy security group allows only the runner; access logs remain auditable.

# [m8-14] Platform forward proxy for GitHub access from evaluation containers

English | [中文](m8-14-github-egress-proxy.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
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
  - tests/deploy/**
  - deploy/**
  - .github/workflows/deploy.yml
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

- Add a backend setting `RUNNER_EGRESS_PROXY` (default empty) delivered with the evaluation task payload, plus **dynamic egress tokens** instead of static credentials.
- The platform issues a short-lived signed token per run (scope=egress, TTL=case timeout plus grace, bound to run/shard/attempt); the payload carries it; the runner uses it as the proxy credential (Basic username slot); a proxy auth helper validates signature, expiry and binding.
- Proxy deployment: a private listener (3128, optional no auth for VPC pools) and a public listener (3129, dynamic-token auth); only GitHub domains are allowed, everything else is denied and logged; `NO_PROXY` keeps the platform, npm mirror and OSS direct; only the commands that need GitHub get the proxy.
- Cover VPC fixed pools, Alibaba spot pools and the external Mac mini.
- Tests: token issuance/expiry/forgery rejection, runner injection with credentials, proxy domain allowlist, private/public paths.

## Acceptance

- The evaluation container downloads from GitHub live through the proxy; expired/forged tokens and non-allowlisted domains are rejected; no static accounts.
- VPC fixed/spot pools use the private 3128 path; the external Mac mini uses the public 3129 path with a dynamic token.
- The allowlist and access logs are auditable; tests and CI pass.

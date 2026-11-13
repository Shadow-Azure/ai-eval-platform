# [m8-15] Build the egress proxy image in CI (fix the deploy failure from the missing squid tag)

English | [中文](m8-15-proxy-image-ci.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-15-proxy-image-ci.*
  - .agents/flow/issues/m8-14-github-egress-proxy.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/**
  - .github/workflows/deploy.yml
  - docs/**
  - tests/deploy/**
  - plans/**
adr: []
github:
  number: 211
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/211
```

## Background

- After #210 merged, the production deploy failed building the proxy image with `docker.io/library/squid:6.10: not found` (that tag does not exist on Docker Hub); the whole deploy failed, the rollback failed too, and 3129 never came up.
- The original design built the proxy on the ECS host via `docker compose`, requiring host access to Docker Hub / the Ubuntu archive — unsuitable and not reproducible in production.

## Goals

- Build and push the proxy image in CI: the Dockerfile uses the available `ubuntu/squid`; the deploy workflow's build job produces `ai-eval-platform-proxy@digest`.
- The deploy pulls that image: the Deploy step passes `PROXY_IMAGE`, `deploy.sh` writes `RUNNER_EGRESS_PROXY_IMAGE` and includes proxy in `docker compose pull`; no host build.
- Document the image source (`ubuntu/squid`).

## Acceptance

- The deploy succeeds: the proxy container runs, 3128/3129 listen; 3129 returns 407 without a token and reaches GitHub with a valid token.
- A production evaluation installs the GitHub CLI through the proxy and runs design→propose→apply.

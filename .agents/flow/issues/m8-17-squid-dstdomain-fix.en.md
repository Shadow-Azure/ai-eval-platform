# [m8-17] Fix the duplicate squid dstdomain entry that prevented the proxy from starting

English | [中文](m8-17-squid-dstdomain-fix.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-17-squid-dstdomain-fix.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/**
  - tests/deploy/**
  - docs/**
  - plans/**
adr: []
github:
  number: 215
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/215
```

## Background

- The #214 deploy diagnostics gave the cause directly: squid failed with `ERROR: '.github.com' is a subdomain of 'github.com'` and `FATAL: Bungled ... acl github_domains dstdomain github.com .github.com ...`, exited, so 3128/3129 never listened and the evaluation install hit `ECONNREFUSED`.
- The allowlist ACL listed both `github.com` and `.github.com`; squid treats `.github.com` as a duplicate of the bare domain and rejects the config.

## Goals

- Remove the redundant `.github.com`, keeping `github.com`, the explicit subdomains (api/codeload/objects/release-assets/github-releases) and `.githubusercontent.com`.
- Update the allowlist description in the docs; the deployment contract test stays green.

## Acceptance

- Squid starts and 3128/3129 listen; 3129 returns 407 without a token.
- A production evaluation installs through the proxy and runs design→propose→apply.

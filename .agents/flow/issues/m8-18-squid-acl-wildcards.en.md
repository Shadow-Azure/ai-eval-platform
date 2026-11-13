# [m8-18] Fix the squid dstdomain wildcard/subdomain overlap

English | [中文](m8-18-squid-acl-wildcards.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-18-squid-acl-wildcards.*
  - .agents/flow/issues/m8-16-proxy-listen-fix.*
  - .agents/flow/issues/m8-17-squid-dstdomain-fix.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/**
  - tests/deploy/**
  - docs/**
  - plans/**
adr: []
github:
  number: 217
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/217
```

## Background

- The #216 deploy diagnostics showed the next squid config error: `ERROR: 'github-releases.githubusercontent.com' is a subdomain of '.githubusercontent.com'`, so the config stayed fatal and the proxy never listened.
- The ACL mixed the `.githubusercontent.com` wildcard with its own subdomains (objects/release-assets/github-releases); squid does not allow a wildcard and its subdomains in the same dstdomain ACL.

## Goals

- Keep exactly two wildcards: `.github.com` (github.com plus its api/codeload subdomains) and `.githubusercontent.com` (objects/release-assets/github-releases and all other asset hosts).
- Update the deployment contract test and the allowlist description in the docs.

## Acceptance

- Squid starts and 3128/3129 listen; 3129 returns 407 without a token.
- A production evaluation installs through the proxy and runs design→propose→apply.

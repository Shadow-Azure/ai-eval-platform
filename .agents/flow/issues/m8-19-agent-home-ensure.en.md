# [m8-19] Ensure /home/eval exists before the asset install

English | [中文](m8-19-agent-home-ensure.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-19-agent-home-ensure.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - runner/scripts/real-task.mjs
  - .github/workflows/backend-ci.yml
  - tests/runner/**
  - plans/**
adr: []
github:
  number: 219
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/219
```

## Background

- The proxy (3128/3129) is fixed and listening, but two production evaluations failed at the runner's pre-install: npm reported `ENOENT: no such file or directory, mkdir '/home/eval/lib'` because `/home/eval` did not exist when the asset install ran as the eval user.
- The same image passed in an m8-15 evaluation, so this is container initialization/order instability; today only the adapter installScript's trailing `mkdir -p /home/eval` creates the home, with no fallback or diagnostics.

## Goals

- Before the asset install, the runner idempotently runs `mkdir -p /home/eval && chown eval:eval /home/eval` as root and logs `id` plus `ls -ld /home /home/eval`.
- A failure of that step is logged clearly, never silent.

## Acceptance

- The asset install no longer fails with `/home/eval` ENOENT.
- The proxied (3129) install and design→propose→apply continue to be verified.

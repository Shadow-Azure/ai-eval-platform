# [m8-20] Use a writable /tmp/eval-home for the asset install and the agent

English | [中文](m8-20-eval-home-writable.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-20-eval-home-writable.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - runner/scripts/real-task.mjs
  - tests/runner/**
  - plans/**
adr: []
github:
  number: 221
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/221
```

## Background

- m8-19 already makes the runner create `/home/eval` as root before the asset install, but evaluations still fail with `ENOENT: mkdir '/home/eval/lib'`: in this image `/home/eval` is not visible/stable for the eval user, so mkdir alone cannot fix it.

## Goals

- Run the asset install with a guaranteed-writable `HOME=/tmp/eval-home` and install the CLI into `$HOME/bin`.
- Add `/tmp/eval-home/bin` to the agent PATH so `.claude/commands/opsx/*` can call `codespec`.
- Before the install, log `id` and `ls -ld /home /home/eval /tmp/eval-home` for diagnosis.

## Acceptance

- The asset install no longer fails with the `/home/eval` ENOENT, and the CLI is callable by the agent.
- The proxied (3129) GitHub download and design→propose→apply can be verified.

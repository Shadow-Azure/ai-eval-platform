# Connect a fixed self-hosted Linux runner and heartbeat

English | [中文](m2-04-fixed-linux-runner.md)

```yaml flow
kind: issue
milestone: m2
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m2-04-fixed-linux-runner.*
  - runner/**
  - backend/**
  - web/**
  - deploy/**
  - .github/workflows/runner-smoke.yml
  - .github/workflows/deploy.yml
  - infra/runner/**
  - scripts/**
  - tests/**
adr: []
github:
  number: 14
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/14
```

## Background

Before introducing spot provisioning, the platform must prove that one fixed Linux machine can register safely, claim platform jobs, execute with isolation, and report liveness. Fixed capacity separates runner connectivity from cloud-instance lifecycle.

## Goals

- Register a self-hosted runner with stable labels on one fixed Linux host.
- Provide minimal persistence and querying for runner heartbeats and online, offline, and busy state.
- Use read-only repository access and short-lived task credentials without storing long-lived platform secrets on the runner.
- Record runner restarts, losses, and recovery, with at most one active job per runner.

## Acceptance

- The platform displays the fixed runner's online state, labels, latest heartbeat, and current task.
- The runner claims a synthetic job from the GitHub queue, executes the container script, and submits heartbeats.
- A restart or heartbeat timeout marks the runner offline, and re-registration makes it available again.
- Runner logs and process environments contain no long-lived model keys, cloud access keys, or platform administrator tokens.

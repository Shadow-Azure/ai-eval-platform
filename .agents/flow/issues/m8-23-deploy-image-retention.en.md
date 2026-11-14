# [m8-23] Prune old images after deploy so the system disk stays bounded

English | [中文](m8-23-deploy-image-retention.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - .agents/flow/issues/m8-23-deploy-image-retention.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/deploy.sh
  - deploy/common.sh
  - .github/workflows/deploy.yml
  - tests/deploy/**
  - plans/**
adr: []
github:
  number: 228
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/228
```

## Background

On 2026-11-14 the deploy host's 40 GB system disk was 100% full, with Docker images consuming 22.5 GB. The 71 deploys since 2026-11-08 each pulled a new api/web image by digest, while neither `deploy/deploy.sh` nor the deploy workflow pruned or retained images, leaving 68 old api and 68 old web versions. Business data and logs totaled less than 1 GB; a manual cleanup reclaimed about 18–20 GB, but continued deploys would fill the disk again.

## Goals

- After a successful deploy, delete local images that are not used by running containers and are outside the retention window, keeping the running images plus the last N rollback versions (N bounded, 2–3 suggested; the digests referenced by `.state/previous.env` must be kept).
- Make the cleanup idempotent and log kept/removed counts and `df` headroom; on failure emit explicit logs without failing an otherwise completed deploy.
- Do not require a person to log in to the host to run cleanup.

## Acceptance

- After several consecutive deploys, `docker system df` image usage stays bounded, old versions outside the window are removed, and the rollback target remains usable.
- Tests under `deploy/` cover the retention boundary: running images, previous digests, and versions to delete.
- Production verification: after a deploy `df -h /` shows recovered and sustained free space, and the cleanup log is visible in the GitHub Actions output.

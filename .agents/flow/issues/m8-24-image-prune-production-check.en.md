# [m8-24] Verify the real prune deletion path in production

English | [中文](m8-24-image-prune-production-check.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - .agents/flow/issues/m8-24-image-prune-production-check.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - deploy/common.sh
  - deploy/deploy.sh
  - tests/deploy/**
  - plans/**
adr: []
github:
  number: 231
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/231
```

## Background

The post-deploy image prune from m8-23 shipped with #230. Production Deploy #43546086523 on 2026-11-14 already ran the cleanup, but each platform repository happened to have exactly three versions, so the log read `retention=3 candidates=8 kept=8 removed=0 failed=0` and the real-daemon deletion path was never triggered. Fake-docker tests cover the deletion boundary, but whether `docker image rm <id>` succeeds for digest-pulled images whose tag is `<none>` still needs confirmation on the next deploy that produces a fourth version.

## Goals

- On a later deploy that produces a fourth image version, confirm the cleanup log shows `removed>0` and `failed=0`, while running images, the `.state/previous.env` rollback digest and the newest 3 versions per repository are kept.
- If real deletion fails (for example the digest reference requires `-f` or `repo@digest`), fix the removal form and add tests.
- This verification needs no new cloud resources.

## Acceptance

- The production Deploy log shows `deploy: image prune: ... removed>0 ... failed=0`, and `df -h /` free space does not shrink as images accumulate.
- After deletion api/web/worker/proxy are healthy and the rollback digest is still present.
- If a fix is needed, `tests/deploy/image-retention.test.mjs` covers the real reference form and CI passes.
